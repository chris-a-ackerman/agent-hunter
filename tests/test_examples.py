import json
import re
from pathlib import Path

import pytest

from contracts import Invoice, JobPosting
from data.synth import check_answer_key
from examples.make_examples import (
    EXAMPLES_DIR,
    INVOICE_EXAMPLES,
    N_REAL_DOCS,
    REQUESTER_ID,
    build_invoice_posting,
    build_job_real,
    build_reject_posting,
)

NAMES = ["invoice_clean", "invoice_long", "invoice_mixed", "reject_unscorable"]
JOB_REAL = Path("data/pools/job_real.json")


def load(name: str) -> JobPosting:
    return JobPosting.model_validate_json((EXAMPLES_DIR / f"{name}.json").read_text())


@pytest.mark.parametrize("name", NAMES)
def test_all_three_load_as_job_postings_with_demo_budgets(name):
    p = load(name)
    assert p.requester_id == REQUESTER_ID
    assert (p.n_real_docs, p.job_budget_usd, p.trial_budget_usd) == (15, 5.00, 0.50)
    assert p.deadline is None and p.deadline_open_ack and p.region is None


@pytest.mark.parametrize("name", ["invoice_clean", "invoice_long", "invoice_mixed"])
def test_every_sample_path_resolves_and_has_a_consistent_answer(name):
    p = load(name)
    assert len(p.samples) == 3
    for s in p.samples:
        pdf = EXAMPLES_DIR / s.path  # relative to the posting file
        assert pdf.is_file() and pdf.read_bytes().startswith(b"%PDF")
        assert s.answer is not None
        check_answer_key(s.answer)


def test_invoice_postings_ask_for_the_invoice_schema():
    for name in ["invoice_clean", "invoice_long", "invoice_mixed"]:
        assert load(name).output_schema == Invoice.model_json_schema()


def test_long_posting_has_long_tables_and_clean_has_short_ones():
    clean = [len(s.answer.line_items) for s in load("invoice_clean").samples]
    long_ = [len(s.answer.line_items) for s in load("invoice_long").samples]
    assert all(2 <= n <= 5 for n in clean)
    assert all(15 <= n <= 30 for n in long_)
    assert {s.answer.currency for s in load("invoice_long").samples} == {"USD"}


def test_mixed_posting_has_scans_and_missing_tax():
    from core.agents.trial_builder import has_text_layer

    samples = load("invoice_mixed").samples
    profiles = [
        (s.answer.tax is not None, not has_text_layer(EXAMPLES_DIR / s.path)) for s in samples
    ]
    assert profiles == [(True, True), (False, False), (False, True)]  # (with tax, scanned)


def test_reject_posting_is_unscorable_but_passes_the_pre_checks():
    p = load("reject_unscorable")
    assert p.output_schema == {"type": "string"}
    assert p.samples == []
    assert p.job_budget_usd > 0 and p.trial_budget_usd > 0


def test_committed_postings_match_their_seeds(tmp_path):
    """The committed JSON is exactly what make_examples produces (no hand edits drift in)."""
    for ex in INVOICE_EXAMPLES:
        assert build_invoice_posting(ex, examples_dir=tmp_path, render=False) == load(ex.name)
    assert build_reject_posting() == load("reject_unscorable")


def test_job_real_manifest_matches_its_seeds(tmp_path):
    ids = json.loads(JOB_REAL.read_text())
    assert len(ids) == len(set(ids)) == N_REAL_DOCS * len(INVOICE_EXAMPLES)
    assert all(re.fullmatch(r"[0-9a-f]{12}", i) for i in ids)  # opaque doc IDs (D17)
    regenerated = [
        i
        for ex in INVOICE_EXAMPLES
        for i in build_job_real(ex, docs_dir=tmp_path / "d", truth_dir=tmp_path / "t", render=False)
    ]
    assert regenerated == ids


def test_samples_and_job_real_documents_never_overlap(tmp_path):
    """A requester sample must never also be a held-out real-job document (plan §5.3)."""
    truth_dir = tmp_path / "t"
    for ex in INVOICE_EXAMPLES:
        build_job_real(ex, docs_dir=tmp_path / "d", truth_dir=truth_dir, render=False)
    real = [Invoice.model_validate_json(f.read_text()) for f in truth_dir.iterdir()]
    assert len(real) == N_REAL_DOCS * len(INVOICE_EXAMPLES)
    for ex in INVOICE_EXAMPLES:
        for s in load(ex.name).samples:
            assert s.answer not in real
