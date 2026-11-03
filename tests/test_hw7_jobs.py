"""The HW7 benchmark job set (AGE-34, experiments/hw7/PREREG.md §2–3)."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from contracts import Invoice, JobPosting, JobSpec
from core.agents.trial_builder import build_trial, has_text_layer
from data import docile_loader
from data.pools import PoolViolation
from data.synth import check_answer_key
from experiments.hw7 import make_jobs
from experiments.hw7.make_jobs import (
    DOCILE_JOBS,
    JOB_IDS,
    JOBS_DIR,
    N_HELDOUT,
    N_SAMPLES,
    REQUESTER_ID,
    SYNTH_JOBS,
    build_synth_heldout,
    build_synth_posting,
    describe_real,
    docile_entry,
    docile_selection,
    merge_job_real,
    synth_entry,
)

JOB_REAL = Path("data/pools/job_real.json")
MANIFEST = json.loads((JOBS_DIR / "manifest.json").read_text())["jobs"]
ENTRIES = {e["job"]: e for e in MANIFEST}
HAVE_DOCILE = (docile_loader.DOCILE_DIR / "annotations").is_dir()
needs_docile = pytest.mark.skipif(not HAVE_DOCILE, reason="data/docile/ not downloaded")


def load(job: str) -> JobPosting:
    return JobPosting.model_validate_json((JOBS_DIR / f"{job}.json").read_text())


def job_param(job: str):
    marks = [] if job in SYNTH_JOBS else [needs_docile]
    return pytest.param(job, marks=marks, id=job)


ALL_JOBS = [job_param(j) for j in JOB_IDS]


# --- manifest ---------------------------------------------------------------------------------


def test_manifest_lists_the_12_jobs_with_the_preregistered_types_and_splits():
    assert [e["job"] for e in MANIFEST] == JOB_IDS
    types = [e["type"] for e in MANIFEST]
    assert types[:8] == ["clean", "clean", "scanned", "scanned", "long", "long"] + ["non_usd"] * 2
    assert types[8:] == ["clean", "scanned", "long", "non_usd"]
    assert [e["split"] for e in MANIFEST] == ["tuning"] * 8 + ["fresh"] * 4
    assert [e["job"] for e in MANIFEST if e["source"] == "docile"] == ["j02", "j09"]
    assert [e["job"] for e in MANIFEST if e["near_tie"]] == ["j01"]
    assert {e["layout"] for e in MANIFEST if e["source"] == "synthetic"} == {
        "classic",
        "modern",
        "compact",
    }
    assert {ENTRIES["j07"]["currency"], ENTRIES["j08"]["currency"]} == {"EUR", "GBP"}
    assert ENTRIES["j12"]["currency"] != "USD"


def test_every_job_has_12_held_out_ids_all_in_job_real_and_none_shared():
    job_real = json.loads(JOB_REAL.read_text())
    assert len(job_real) == len(set(job_real))
    heldout = [i for e in MANIFEST for i in e["heldout_doc_ids"]]
    assert all(len(e["heldout_doc_ids"]) == N_HELDOUT for e in MANIFEST)
    assert len(set(heldout)) == len(heldout) == N_HELDOUT * 12
    assert set(heldout) <= set(job_real)
    assert job_real[-len(heldout) :] == heldout  # appended after the examples' 45


def test_synthetic_manifest_entries_and_postings_match_their_seeds(tmp_path):
    """Committed JSON is exactly what make_jobs produces (the same check as make_examples)."""
    for job in SYNTH_JOBS.values():
        heldout = build_synth_heldout(
            job, docs_dir=tmp_path / "d", truth_dir=tmp_path / "t", render=False
        )
        assert synth_entry(job, heldout) == ENTRIES[job.job]
        assert build_synth_posting(job, jobs_dir=tmp_path, render=False) == load(job.job)


def test_docile_manifest_entries_follow_the_preregistered_rule():
    selected = docile_selection()
    assert len(selected) == len(set(selected)) == 30
    assert ENTRIES["j02"]["docile_ids"] == selected[:15]
    assert ENTRIES["j09"]["docile_ids"] == selected[15:]
    for job in DOCILE_JOBS.values():
        e = ENTRIES[job.job]
        assert docile_entry(job, selected, e["heldout_doc_ids"]) == e
        assert e["layout"] is e["sample_seed"] is e["heldout_seed"] is None


def test_merge_job_real_replaces_old_ids_and_keeps_the_rest_in_place():
    assert merge_job_real(["a", "b", "x", "y"], old=["x", "y"], new=["y", "z"]) == [
        "a",
        "b",
        "y",
        "z",
    ]


# --- postings ---------------------------------------------------------------------------------


@pytest.mark.parametrize("job", ALL_JOBS)
def test_every_posting_validates_with_the_benchmark_budgets(job):
    p = load(job)
    assert p.requester_id == REQUESTER_ID
    assert (p.n_real_docs, p.job_budget_usd, p.trial_budget_usd) == (15, 5.00, 0.50)
    assert p.deadline is None and p.deadline_open_ack and p.region is None
    assert p.output_schema == Invoice.model_json_schema()
    assert len(p.samples) == N_SAMPLES
    for n, s in enumerate(p.samples, start=1):
        assert s.path == f"samples/{job}/sample_{n}.pdf"
        assert (JOBS_DIR / s.path).read_bytes().startswith(b"%PDF")
        assert s.answer is not None


@pytest.mark.parametrize("job", list(SYNTH_JOBS))
def test_synthetic_samples_match_the_job_settings(job):
    j = SYNTH_JOBS[job]
    lo, hi = j.line_items
    for s in load(job).samples:
        assert s.answer is not None
        check_answer_key(s.answer)
        assert s.answer.currency == j.currency
        assert lo <= len(s.answer.line_items) <= hi
        scanned = not has_text_layer(JOBS_DIR / s.path)
        assert (s.answer.tax is not None, scanned) in j.profiles
    if j.type == "scanned":
        assert all(scanned for _, scanned in j.profiles)
    if j.type == "long":
        assert lo >= 15


def test_describe_real_reports_counts_scans_and_tax():
    inv = {
        "vendor_name": "A",
        "invoice_number": "1",
        "invoice_date": "2020-01-01",
        "currency": "USD",
        "subtotal": None,
        "total": 1.0,
    }
    one = {"description": "x", "quantity": 1, "unit_price": 1.0, "amount": 1.0}
    answers = [
        Invoice.model_validate({**inv, "line_items": [one] * n, "tax": tax})
        for n, tax in [(1, None), (3, 0.0), (2, None)]
    ]
    text = describe_real(answers, scanned=[True, False, False], pages=[1, 1, 2])
    assert "from one business vendor" in text and "1 to 3 line items each, typically 2" in text
    assert "1 of 3 scanned" in text and "one or two pages" in text and "all in USD" in text
    assert "2 of 3 show no separate tax amount" in text


def test_without_docile_the_docile_jobs_are_skipped_with_a_warning(tmp_path, monkeypatch, capsys):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    (jobs_dir / "manifest.json").write_text((JOBS_DIR / "manifest.json").read_text())
    pools = tmp_path / "pools"
    pools.mkdir()
    shutil.copy(JOB_REAL, pools / "job_real.json")
    monkeypatch.setattr(make_jobs, "JOBS_DIR", jobs_dir)
    monkeypatch.setattr(make_jobs, "POOLS_DIR", pools)
    monkeypatch.setattr(make_jobs, "DOCS_DIR", tmp_path / "d")
    monkeypatch.setattr(make_jobs, "TRUTH_DIR", tmp_path / "t")
    monkeypatch.setattr(docile_loader, "DOCILE_DIR", tmp_path / "no-docile")

    make_jobs.main(["--no-render"])

    assert "skipping j02" in capsys.readouterr().err
    assert not (jobs_dir / "j02.json").exists() and not (jobs_dir / "j09.json").exists()
    assert (jobs_dir / "manifest.json").read_text() == (JOBS_DIR / "manifest.json").read_text()
    assert (pools / "job_real.json").read_text() == JOB_REAL.read_text()


# --- the trial never touches the held-out pile ------------------------------------------------


@pytest.mark.parametrize("job", ALL_JOBS)
def test_trials_raise_no_pool_violation_and_share_nothing_with_the_held_out_pile(job, tmp_path):
    posting = load(job)
    heldout = set(ENTRIES[job]["heldout_doc_ids"])
    for repeat in ("r1", "r2"):
        job_id = f"hw7-{job}-{repeat}"
        spec = JobSpec(
            job_id=job_id,
            decision="approve",
            reason="ok",
            job_type="invoice_extraction",
            n_real_docs=posting.n_real_docs,
            per_doc_budget_usd=posting.job_budget_usd / posting.n_real_docs,
        )
        try:
            trial = build_trial(
                spec,
                posting,
                size=12,
                samples_dir=JOBS_DIR,
                docs_dir=tmp_path / repeat / "docs",
                truth_dir=tmp_path / repeat / "truth",
                render=False,
            )
        except PoolViolation as e:  # pragma: no cover - the failure we're guarding against
            pytest.fail(f"{job_id}: {e}")
        assert len(trial.documents) == 12
        assert not {d.doc_id for d in trial.documents} & heldout


# --- real invoices (DocILE) -------------------------------------------------------------------


@needs_docile
def test_docile_jobs_have_3_samples_and_12_held_out_with_no_shared_ids():
    mapping = json.loads(docile_loader.MAP_PATH.read_text())
    by_docid = {v: k for k, v in mapping.items()}
    job_real = set(json.loads(JOB_REAL.read_text()))
    ids = {}
    for job in DOCILE_JOBS:
        e = ENTRIES[job]
        assert len(load(job).samples) == N_SAMPLES
        assert len(e["docile_ids"]) == N_SAMPLES + N_HELDOUT
        assert set(e["docile_ids"]) <= set(mapping.values())
        assert e["heldout_doc_ids"] == [by_docid[d] for d in e["docile_ids"][N_SAMPLES:]]
        assert set(e["heldout_doc_ids"]) <= job_real
        assert not {by_docid[d] for d in e["docile_ids"][:N_SAMPLES]} & job_real
        ids[job] = set(e["docile_ids"])
    assert not ids["j02"] & ids["j09"]


@needs_docile
@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_docile_pdfs_and_postings_stay_out_of_git():
    paths = [f"experiments/hw7/jobs/{j}.json" for j in DOCILE_JOBS] + [
        f"experiments/hw7/jobs/samples/{j}/sample_{n}.pdf"
        for j in DOCILE_JOBS
        for n in range(1, N_SAMPLES + 1)
    ]
    ignored = subprocess.run(
        ["git", "check-ignore", *paths], capture_output=True, text=True
    ).stdout.split()
    assert sorted(ignored) == sorted(paths)
    tracked = subprocess.run(
        ["git", "ls-files", "experiments/hw7/jobs"], capture_output=True, text=True
    ).stdout.split()
    assert not [p for p in tracked if "/j02" in p or "/j09" in p]
