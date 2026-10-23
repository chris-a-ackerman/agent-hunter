import ast
from pathlib import Path

import pytest
from mypy import api as mypy_api

from contracts import ScoreReport, Submission
from core.eval import scorer as real_scorer
from core.pipeline.config import get_scorer, scorer_mode
from mocks import fake_scorer
from tests.test_contracts import examples as ex


def sub(provider, doc_id, status="ok", output=None):
    if status == "ok" and output is None:
        output = ex.INVOICE.model_dump()
    return Submission(
        task_id=f"{provider}:{doc_id}",
        doc_id=doc_id,
        provider=provider,
        status=status,
        output=output,
        latency_ms=None if status == "not_run" else 100,
        cost_usd=None,
    )


SUBS = [
    sub("acme@1", ex.DOC_A),
    sub("acme@1", ex.DOC_B),
    sub("bolt@2", ex.DOC_A),
    sub("bolt@2", ex.DOC_B, status="timeout"),
    sub("cora@1", ex.DOC_A, output={"text": "not json"}),
    sub("cora@1", ex.DOC_B, status="not_run"),
]


def test_reports_are_schema_valid_and_one_per_provider():
    reports = fake_scorer.score(ex.SCORING_CONFIG, SUBS)
    assert [r.provider for r in reports] == ["acme@1", "bolt@2", "cora@1"]
    for r in reports:
        ScoreReport.model_validate(r.model_dump())
        assert r.n_docs == len(r.doc_scores) == 2
        assert [d.doc_id for d in r.doc_scores] == [ex.DOC_A, ex.DOC_B]
        assert r.ci_95[0] <= r.ci_95[1]
        assert r.trial_score == pytest.approx(sum(d.score for d in r.doc_scores) / 2)
        for d in r.doc_scores:
            assert set(d.field_scores) == set(ex.SCORING_CONFIG.weights)
            assert set(d.field_scores.values()) == {d.score}
            assert d.sum_check_ok is None


def test_same_inputs_identical_output():
    a = fake_scorer.score(ex.SCORING_CONFIG, SUBS)
    b = fake_scorer.score(ex.SCORING_CONFIG, SUBS)
    assert [r.model_dump() for r in a] == [r.model_dump() for r in b]


def test_trial_id_changes_scores():
    other = ex.SCORING_CONFIG.model_copy(update={"trial_id": "trial-2"})
    a = fake_scorer.score(ex.SCORING_CONFIG, SUBS)[0]
    b = fake_scorer.score(other, SUBS)[0]
    assert a.trial_score != b.trial_score


def test_ok_scores_near_base_accuracy():
    acme = fake_scorer.score(ex.SCORING_CONFIG, SUBS)[0]
    assert all(0.4 <= d.score <= 1.0 and d.status == "ok" for d in acme.doc_scores)


def test_non_ok_score_zero():
    reports = {r.provider: r for r in fake_scorer.score(ex.SCORING_CONFIG, SUBS)}
    timeout = reports["bolt@2"].doc_scores[1]
    assert (timeout.status, timeout.score) == ("timeout", 0.0)
    invalid, not_run = reports["cora@1"].doc_scores
    assert (invalid.status, invalid.score) == ("invalid", 0.0)
    assert (not_run.status, not_run.score) == ("not_run", 0.0)
    assert reports["cora@1"].trial_score == 0.0
    assert reports["cora@1"].ci_95 == (0.0, 0.0)


def test_unknown_doc_rejected_like_real_scorer():
    bad = [sub("acme@1", "ffffffffffff")]
    with pytest.raises(ValueError):
        fake_scorer.score(ex.SCORING_CONFIG, bad)
    with pytest.raises(ValueError):
        real_scorer.score(ex.SCORING_CONFIG, bad)


def test_empty_submissions():
    assert fake_scorer.score(ex.SCORING_CONFIG, []) == []


def test_no_import_from_core_eval():
    tree = ast.parse(Path(fake_scorer.__file__).read_text())
    modules = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    modules += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not [m for m in modules if m.startswith("core")]


def test_satisfies_score_protocol():
    check = (
        "from contracts.steps import Score\nfrom mocks.fake_scorer import score\ns: Score = score\n"
    )
    stdout, stderr, status = mypy_api.run(["--no-incremental", "-c", check])
    assert status == 0, stdout + stderr


@pytest.mark.parametrize(
    ("env", "mode", "fn"),
    [
        (None, "mock", fake_scorer.score),
        ("mock", "mock", fake_scorer.score),
        ("REAL", "real", real_scorer.score),
    ],
)
def test_scorer_switch(monkeypatch, env, mode, fn):
    if env is None:
        monkeypatch.delenv("SCORER", raising=False)
    else:
        monkeypatch.setenv("SCORER", env)
    assert scorer_mode() == mode
    assert get_scorer() is fn


def test_scorer_switch_rejects_unknown(monkeypatch):
    monkeypatch.setenv("SCORER", "fake")
    with pytest.raises(ValueError):
        get_scorer()
