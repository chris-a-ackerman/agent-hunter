import json
import logging
import shutil
from pathlib import Path

import pytest

from contracts import JobPosting, JobSpec, SampleDoc, TrialPackage
from core.agents.trial_builder import (
    COMPONENT,
    MAX_SAMPLES,
    build_trial,
    fallback_settings,
)
from core.eval.stats import seed_for
from data.pools import PoolViolation, check_access, load_doc, load_pool
from data.synth import generate
from examples.make_examples import EXAMPLES_DIR
from gateway import DEFAULT_MODEL
from mocks.fake_gateway import FakeGateway

MODERN_EUR = {"layout": "modern", "currency": "EUR", "line_items": [3, 6]}


def posting(name: str) -> JobPosting:
    return JobPosting.model_validate_json((EXAMPLES_DIR / f"{name}.json").read_text())


def spec(job_id: str = "job-trial-01") -> JobSpec:
    return JobSpec(
        job_id=job_id, decision="approve", reason="ok", n_real_docs=15, per_doc_budget_usd=0.33
    )


@pytest.fixture
def dirs(tmp_path: Path) -> dict[str, Path]:
    pools = tmp_path / "pools"
    pools.mkdir()
    shutil.copyfile("data/pools/job_real.json", pools / "job_real.json")
    return {
        "samples_dir": EXAMPLES_DIR,
        "docs_dir": tmp_path / "docs",
        "truth_dir": tmp_path / "truth",
        "pools_dir": pools,
    }


def build(p: JobPosting, dirs, *replies, s: JobSpec | None = None, **kw) -> TrialPackage:
    gw = FakeGateway(*replies) if replies else None
    return build_trial(s or spec(), p, gateway=gw, render=False, **dirs, **kw)  # type: ignore[arg-type]


# --- acceptance: valid TrialPackage for both invoice examples ---------------------------------


@pytest.mark.parametrize("name", ["invoice_clean", "invoice_long"])
def test_valid_trial_for_invoice_examples(name, dirs):
    trial = build(posting(name), dirs, MODERN_EUR)
    TrialPackage.model_validate(trial.model_dump())
    assert trial.job_id == "job-trial-01"
    assert len(trial.documents) == 6
    sources = [d.source for d in trial.documents]
    assert sources.count("sample") == 3 and sources.count("synthetic") == 3
    assert len({d.doc_id for d in trial.documents}) == 6
    for d in trial.documents:
        truth = json.loads((dirs["truth_dir"] / f"{d.doc_id}.json").read_text())
        assert truth == json.loads(d.answer.model_dump_json())
        if d.source == "sample":
            assert (dirs["docs_dir"] / f"{d.doc_id}.pdf").is_file()


def test_renders_synthetic_pdfs(dirs):
    trial = build_trial(spec(), posting("invoice_clean"), size=4, **dirs)
    for d in trial.documents:
        assert (dirs["docs_dir"] / f"{d.doc_id}.pdf").is_file()


# --- acceptance: job_real is off limits -------------------------------------------------------


def test_job_real_doc_passed_to_trial_builder_raises(dirs, tmp_path):
    """A posting whose sample is one of our held-out job_real documents."""
    job_real_id = sorted(load_pool("job_real", pools_dir=dirs["pools_dir"]))[0]
    leak_dir = tmp_path / "leak"
    leak_dir.mkdir()
    (leak_dir / f"{job_real_id}.pdf").write_bytes(b"%PDF-1.4")
    p = posting("invoice_clean")
    p = p.model_copy(
        update={"samples": [SampleDoc(path=f"{job_real_id}.pdf", answer=p.samples[0].answer)]}
    )
    with pytest.raises(PoolViolation, match="job_real"):
        build(p, {**dirs, "samples_dir": leak_dir})


def test_synthetic_doc_that_is_in_job_real_raises(dirs):
    """If the generator ever recreates a job_real document, the guard stops it."""
    s = spec()
    p = posting("invoice_clean")
    p = p.model_copy(update={"samples": []})
    first = generate(
        layout="classic",
        n=1,
        seed=seed_for(s.job_id),
        line_items=(1, 8),
        render=False,
        truth_dir=dirs["truth_dir"],
    )[0]
    (dirs["pools_dir"] / "job_real.json").write_text(json.dumps([first.doc_id]))
    with pytest.raises(PoolViolation):
        build(p, dirs, s=s)


def test_load_doc_guard(dirs):
    job_real_id = sorted(load_pool("job_real", pools_dir=dirs["pools_dir"]))[0]
    with pytest.raises(PoolViolation):
        load_doc(job_real_id, caller="trial_builder", pools_dir=dirs["pools_dir"])
    with pytest.raises(PoolViolation):
        check_access(job_real_id, caller="trial_builder", pools_dir=dirs["pools_dir"])
    check_access(job_real_id, caller="experiment_harness", pools_dir=dirs["pools_dir"])
    check_access("0123456789ab", caller="trial_builder", pools_dir=dirs["pools_dir"])


def test_missing_manifest_is_an_empty_pool(tmp_path):
    assert load_pool("trial_real", pools_dir=tmp_path) == frozenset()
    with pytest.raises(ValueError):
        load_pool("secret", pools_dir=tmp_path)


# --- acceptance: original sample names never leak ---------------------------------------------


def test_no_doc_id_or_file_name_contains_sample_name(dirs):
    p = posting("invoice_long")
    trial = build(p, dirs)
    names = {Path(s.path).stem for s in p.samples} | {"sample", "invoice_long"}
    files = [f.name for f in dirs["docs_dir"].iterdir()] + [
        f.name for f in dirs["truth_dir"].iterdir()
    ]
    for n in names:
        assert not any(n in d.doc_id for d in trial.documents)
        assert not any(n in f for f in files)


# --- samples ----------------------------------------------------------------------------------


def test_samples_without_answers_are_skipped(dirs):
    p = posting("invoice_clean")
    p = p.model_copy(update={"samples": [p.samples[0].model_copy(update={"answer": None})]})
    trial = build(p, dirs)
    assert all(d.source == "synthetic" for d in trial.documents)


def test_at_most_three_samples(dirs):
    p = posting("invoice_clean")
    p = p.model_copy(update={"samples": p.samples * 2})
    trial = build(p, dirs, size=8)
    assert sum(d.source == "sample" for d in trial.documents) == MAX_SAMPLES


def test_small_trial_takes_only_size_samples(dirs):
    trial = build(posting("invoice_clean"), dirs, size=2)
    assert [d.source for d in trial.documents] == ["sample", "sample"]
    assert len(list(dirs["docs_dir"].iterdir())) == 2  # the third sample wasn't copied


def test_order_is_shuffled_and_seeded(dirs):
    orders = {
        tuple(d.source for d in build(posting("invoice_clean"), dirs, s=spec(f"job-{i}")).documents)
        for i in range(8)
    }
    assert len(orders) > 1  # samples aren't always first
    a = build(posting("invoice_clean"), dirs)
    b = build(posting("invoice_clean"), dirs)
    assert [d.source for d in a.documents] == [d.source for d in b.documents]
    synth = [d.doc_id for d in a.documents if d.source == "synthetic"]
    assert synth == [d.doc_id for d in b.documents if d.source == "synthetic"]


# --- generator settings -----------------------------------------------------------------------


def test_llm_settings_are_used(dirs):
    gw = FakeGateway(MODERN_EUR)
    trial = build_trial(spec(), posting("invoice_clean"), gateway=gw, render=False, **dirs)  # type: ignore[arg-type]
    synth = [d.answer for d in trial.documents if d.source == "synthetic"]
    assert all(a.currency == "EUR" and 3 <= len(a.line_items) <= 6 for a in synth)
    [call] = gw.calls
    assert call["component"] == COMPONENT and call["model"] == DEFAULT_MODEL
    assert call["response_format"] == {"type": "json_object"} and call["max_tokens"]
    user = call["messages"][-1]["content"]
    assert '"line_items"' in user and "sample_1" not in user


@pytest.mark.parametrize(
    "reply",
    [
        "not json",
        {"layout": "fancy", "currency": "USD", "line_items": [2, 5]},
        {"layout": "classic", "currency": "JPY", "line_items": [2, 5]},
        {"layout": "classic", "currency": "USD", "line_items": [5, 2]},
        {"layout": "classic", "currency": "USD", "line_items": [0, 500]},
        {"layout": "classic"},
    ],
    ids=["not-json", "bad-layout", "bad-currency", "min-gt-max", "out-of-range", "missing"],
)
def test_bad_settings_fall_back(reply, dirs, caplog):
    p = posting("invoice_long")
    with caplog.at_level(logging.WARNING, logger="core.agents.trial_builder"):
        trial = build(p, dirs, reply)
    assert "using" in caplog.text
    counts = [len(s.answer.line_items) for s in p.samples if s.answer]
    synth = [d.answer for d in trial.documents if d.source == "synthetic"]
    assert all(a.currency == "USD" for a in synth)
    assert all(min(counts) <= len(a.line_items) <= max(counts) for a in synth)


def test_fallback_without_samples_uses_generator_default():
    s = fallback_settings([])
    assert (s.layout, s.currency, s.line_items) == ("classic", "USD", (1, 8))
