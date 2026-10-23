from datetime import UTC, datetime

import pytest

from contracts import Invoice, LineItem, Rubric, ScoringConfig, Tolerances, TrialDoc, TrialPackage
from contracts.steps import BuildEval
from core.eval.eval_builder import EvalBuildError, build_eval

WEIGHTS = {
    "total": 0.30,
    "line_items": 0.30,
    "vendor_name": 0.15,
    "invoice_date": 0.15,
    "invoice_number": 0.05,
    "currency": 0.05,
}
NOW = datetime(2026, 10, 23, tzinfo=UTC)


def answer(**kw) -> Invoice:
    base = dict(
        vendor_name="Acme Supplies",
        invoice_number="INV-1",
        invoice_date="2026-04-17",
        currency="USD",
        line_items=[LineItem(description="Paper", quantity=2, unit_price=5.0, amount=10.0)],
        subtotal=10.0,
        tax=1.0,
        total=11.0,
    )
    return Invoice(**{**base, **kw})


def rubric(**kw) -> Rubric:
    base = dict(
        rubric_id="rub-1",
        job_id="job-1",
        weights=WEIGHTS,
        tolerances=Tolerances(total_abs=0.05),
        required_fields=["total", "vendor_name"],
        consistency_checks=["line_items_sum"],
        approved_at=NOW,
    )
    return Rubric(**{**base, **kw})


def trial(docs=None, **kw) -> TrialPackage:
    if docs is None:
        docs = [
            TrialDoc(doc_id="aaaaaaaaaaaa", source="sample", answer=answer()),
            TrialDoc(doc_id="bbbbbbbbbbbb", source="synthetic", answer=answer(total=22.0)),
        ]
    return TrialPackage(
        **{"trial_id": "trial-1", "job_id": "job-1", "documents": docs, "created_at": NOW, **kw}
    )


def test_happy_path_binds_rubric_to_answer_key():
    cfg = build_eval(rubric(), trial())
    assert isinstance(cfg, ScoringConfig)
    assert cfg.trial_id == "trial-1"
    assert cfg.weights == WEIGHTS
    assert cfg.tolerances == Tolerances(total_abs=0.05)
    assert cfg.consistency_checks == ["line_items_sum"]
    assert cfg.answer_keys == {"aaaaaaaaaaaa": answer(), "bbbbbbbbbbbb": answer(total=22.0)}


def test_satisfies_the_step_protocol():
    step: BuildEval = build_eval
    assert step(rubric(), trial()).trial_id == "trial-1"


def test_output_doesnt_share_state_with_the_rubric():
    r = rubric()
    cfg = build_eval(r, trial())
    cfg.weights["total"] = 0.0
    cfg.consistency_checks.append("x")
    assert r.weights["total"] == 0.30 and r.consistency_checks == ["line_items_sum"]


def test_null_optional_field_is_fine_when_not_required():
    docs = [TrialDoc(doc_id="aaaaaaaaaaaa", source="synthetic", answer=answer(invoice_number=None))]
    build_eval(rubric(), trial(docs))


# --- one test per error case -----------------------------------------------------------------


def _unvalidated_rubric(weights: dict[str, float]) -> Rubric:
    """The Rubric contract rejects bad weights, so build one without validation."""
    return Rubric.model_construct(**{**rubric().model_dump(), "weights": weights})


def test_weights_not_summing_to_one():
    with pytest.raises(EvalBuildError, match="sum to"):
        build_eval(_unvalidated_rubric({**WEIGHTS, "total": 0.5}), trial())


def test_weighted_field_that_isnt_scored():
    with pytest.raises(EvalBuildError, match="aren't scored.*subtotal"):
        build_eval(_unvalidated_rubric({**WEIGHTS, "total": 0.25, "subtotal": 0.05}), trial())


def test_required_field_null_in_an_answer_key():
    docs = [
        TrialDoc(doc_id="aaaaaaaaaaaa", source="sample", answer=answer()),
        TrialDoc(doc_id="bbbbbbbbbbbb", source="synthetic", answer=answer(invoice_number=None)),
    ]
    with pytest.raises(EvalBuildError, match=r"'invoice_number' is null.*bbbbbbbbbbbb"):
        build_eval(rubric(required_fields=["invoice_number"]), trial(docs))


def test_required_field_that_isnt_an_invoice_field():
    with pytest.raises(EvalBuildError, match="aren't Invoice fields.*nonsense"):
        build_eval(rubric(required_fields=["total", "nonsense"]), trial())


def test_duplicate_doc_ids():
    docs = [
        TrialDoc(doc_id="aaaaaaaaaaaa", source="sample", answer=answer()),
        TrialDoc(doc_id="aaaaaaaaaaaa", source="synthetic", answer=answer(total=22.0)),
    ]
    with pytest.raises(EvalBuildError, match="duplicate doc IDs.*aaaaaaaaaaaa"):
        build_eval(rubric(), trial(docs))


def test_empty_trial():
    with pytest.raises(EvalBuildError, match="no documents"):
        build_eval(rubric(), trial([]))


def test_rubric_and_trial_from_different_jobs():
    with pytest.raises(EvalBuildError, match="job-2"):
        build_eval(rubric(job_id="job-2"), trial())


def test_every_problem_is_reported_at_once():
    with pytest.raises(EvalBuildError) as e:
        build_eval(rubric(job_id="job-2", required_fields=["nonsense"]), trial([]))
    msg = str(e.value)
    assert "job-2" in msg and "no documents" in msg and "nonsense" in msg
