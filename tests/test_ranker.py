import logging
from datetime import UTC, datetime, timedelta

import pytest

from contracts import DocScore, ScoreReport, ScoringConfig, Submission, Tolerances, Verdict
from core.agents.ranker import (
    COMPONENT,
    allowed_numbers,
    build_rows,
    invented_numbers,
    rank,
    template_rationale,
)
from core.eval.defaults import DEFAULT_WEIGHTS
from core.eval.scorer import score
from core.eval.stats import decide
from data.synth import generate
from gateway import DEFAULT_MODEL
from mocks.fake_gateway import FakeGateway
from mocks.fake_providers.corruption import MOCKS, mock_output

TRIAL = "trial-rank-01"


def doc_id(i: int) -> str:
    return f"{i:012x}"


def report(provider: str, scores: list[float], ci: tuple[float, float]) -> ScoreReport:
    docs = [
        DocScore(doc_id=doc_id(i), score=s, field_scores={}, status="ok", sum_check_ok=None)
        for i, s in enumerate(scores)
    ]
    return ScoreReport(
        provider=provider,
        doc_scores=docs,
        trial_score=sum(scores) / len(scores),
        ci_95=ci,
        n_docs=len(docs),
    )


def subs(provider: str, statuses: list[str], cost=0.01, latency=1000) -> list[Submission]:
    out = []
    for i, st in enumerate(statuses):
        ok = st == "ok"
        out.append(
            Submission(
                task_id=f"{provider}-{i}",
                doc_id=doc_id(i),
                provider=provider,
                status=st,  # type: ignore[arg-type]
                output={} if ok else None,
                latency_ms=None if st == "not_run" else (latency if ok else 9000),
                cost_usd=cost,
            )
        )
    return out


A, B, C = "alpha@1.0.0", "beta@2.1", "gamma@1"
CLEAR = (
    [report(A, [0.9, 0.95, 0.92], (0.88, 0.96)), report(B, [0.5, 0.6, 0.55], (0.45, 0.62))],
    subs(A, ["ok"] * 3, cost=0.004) + subs(B, ["ok", "ok", "timeout"], cost=0.001),
)
TIE = (
    [report(A, [0.9, 0.8, 0.85], (0.78, 0.92)), report(B, [0.85, 0.8, 0.8], (0.75, 0.88))],
    subs(A, ["ok"] * 3, cost=0.01) + subs(B, ["ok"] * 3, cost=0.002),
)
INSUFFICIENT = (
    [report(A, [0.9, 0.9, 0.9], (0.9, 0.9)), report(B, [0.0, 0.0, 0.0], (0.0, 0.0))],
    subs(A, ["ok"] * 3) + subs(B, ["timeout"] * 3),
)


def rank_with(case, *replies) -> tuple[Verdict, FakeGateway]:
    reports, submissions = case
    gw = FakeGateway(*replies)
    return rank(reports, submissions, TRIAL, gateway=gw), gw  # type: ignore[arg-type]


# --- acceptance: valid Verdicts for both example postings, end to end ------------------------


@pytest.mark.parametrize(
    ("layout", "items", "seed"), [("classic", (2, 5), 2401), ("modern", (15, 30), 2402)]
)
def test_verdict_for_example_postings(layout, items, seed, tmp_path):
    """Trial docs like each example posting's, answered by the mock providers, scored by the
    real Scorer, then ranked."""
    docs = generate(
        layout=layout, n=6, seed=seed, line_items=items, render=False, truth_dir=tmp_path
    )
    config = ScoringConfig(
        trial_id=TRIAL,
        weights=DEFAULT_WEIGHTS,
        tolerances=Tolerances(),
        answer_keys={d.doc_id: d.truth for d in docs},
        consistency_checks=["line_items_sum"],
    )
    submissions = []
    for slug, spec in MOCKS.items():
        for d in docs:
            answered = spec.behaviour != "timeout"
            submissions.append(
                Submission(
                    task_id=f"{slug}-{d.doc_id}",
                    doc_id=d.doc_id,
                    provider=f"{slug}@1.0.0",
                    status="ok" if answered else "timeout",
                    output=mock_output(slug, d.doc_id, d.truth) if answered else None,
                    latency_ms=800 if answered else 30000,
                    cost_usd=spec.cost_usd,
                )
            )
    verdict = rank(score(config, submissions), submissions, TRIAL)
    Verdict.model_validate(verdict.model_dump())
    assert verdict.status in ("recommended", "tie")
    assert verdict.evidence == TRIAL
    assert [r.accuracy for r in verdict.ranking] == sorted(
        (r.accuracy for r in verdict.ranking), reverse=True
    )
    timed_out = next(r for r in verdict.ranking if r.provider == "mock-d@1.0.0")
    assert timed_out.timeouts == 6 and timed_out.mean_latency_ms is None
    assert not invented_numbers(verdict.rationale, verdict.ranking, decide(verdict.ranking))


# --- rows -------------------------------------------------------------------------------------


def test_rows_cost_and_latency_over_ok_only_and_timeouts_counted():
    reports = [report(A, [1.0, 0.0, 0.0], (0.0, 1.0))]
    s = subs(A, ["ok", "timeout", "error"], cost=0.02, latency=500)
    [row] = build_rows(reports, s)
    assert row.cost_per_doc == pytest.approx(0.02)
    assert row.mean_latency_ms == 500
    assert row.timeouts == 1


def test_rows_sorted_by_accuracy():
    reports = [report(B, [0.5], (0.5, 0.5)), report(A, [0.9], (0.9, 0.9))]
    rows = build_rows(reports, subs(A, ["ok"]) + subs(B, ["ok"]))
    assert [r.provider for r in rows] == [A, B]


# --- decision paths ---------------------------------------------------------------------------


def test_recommended_path():
    v = rank(*CLEAR, TRIAL)
    assert v.status == "recommended" and v.recommended == A and not v.tie and v.tied == []
    assert v.trial_accuracy == v.ranking[0].accuracy
    assert v.ci_95 == (0.88, 0.96) and v.cost_per_doc == pytest.approx(0.004)
    assert v.valid_until == datetime.now(UTC).date() + timedelta(days=30)


def test_tie_path_is_valid_verdict():
    v = rank(*TIE, TRIAL)
    Verdict.model_validate(v.model_dump())
    assert v.status == "tie" and v.tie and set(v.tied) == {A, B}
    assert v.recommended == B  # cheaper wins the tie
    assert v.cost_per_doc == pytest.approx(0.002)


def test_insufficient_path_is_valid_verdict_template_only():
    gw = FakeGateway()  # no replies: any model call would fail the test
    v = rank(*INSUFFICIENT, TRIAL, gateway=gw)  # type: ignore[arg-type]
    Verdict.model_validate(v.model_dump())
    assert v.status == "insufficient_candidates" and v.recommended is None
    assert v.trial_accuracy is None and v.ci_95 is None and v.cost_per_doc is None
    assert v.valid_until is None
    assert gw.calls == []
    assert v.rationale == "Only 1 of 2 providers answered the trial, so there is no recommendation."


# --- acceptance: every number in the rationale matches the table -----------------------------


def test_llm_rationale_with_table_numbers_is_kept():
    text = (
        f"{A} is recommended: it scored 92.3% (95% CI 88.0–96.0%) at $0.004 per invoice. "
        f"{B} scored 55% and had 1 timeout, so the 2 providers are clearly separated."
    )
    v, gw = rank_with(CLEAR, text)
    assert v.rationale == text
    [call] = gw.calls
    assert call["component"] == COMPONENT and call["model"] == DEFAULT_MODEL
    assert call["max_tokens"]


def test_number_check_rejects_invented_number_and_falls_back(caplog):
    text = f"{A} scored 92.3%, and it is 40% faster than the rest."
    with caplog.at_level(logging.WARNING, logger="core.agents.ranker"):
        v, _ = rank_with(CLEAR, text)
    assert v.rationale == template_rationale(v.ranking, decide(v.ranking))
    assert "['40']" in caplog.text


def test_empty_llm_rationale_falls_back():
    v, _ = rank_with(TIE, "   ")
    assert v.rationale == template_rationale(v.ranking, decide(v.ranking))


def test_provider_versions_are_not_mistaken_for_numbers():
    rows = build_rows(*CLEAR)
    assert invented_numbers(f"{A} beats {B}.", rows, decide(rows)) == []


@pytest.mark.parametrize("case", [CLEAR, TIE, INSUFFICIENT], ids=["clear", "tie", "insufficient"])
def test_template_passes_its_own_number_check(case):
    v = rank(*case, TRIAL)
    d = decide(v.ranking)
    assert v.rationale == template_rationale(v.ranking, d)
    assert invented_numbers(v.rationale, v.ranking, d) == []


def test_allowed_numbers_cover_rounding_variants():
    rows = build_rows(*CLEAR)
    allowed = allowed_numbers(rows, decide(rows))
    assert {"92", "92.3", "0.00", "0.004", "0.0040", "95", "2"} <= allowed
