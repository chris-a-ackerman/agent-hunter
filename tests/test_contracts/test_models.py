import json

import pytest
from pydantic import BaseModel, ValidationError

from contracts import (
    INVOICE_JSON_SCHEMA,
    MODELS,
    AgentCard,
    Invoice,
    JobPosting,
    ProviderResult,
    Rubric,
    ScoreReport,
    Submission,
    TrialDoc,
    Verdict,
    new_doc_id,
)
from contracts.export import SCHEMAS_DIR, render
from tests.test_contracts import examples as ex


def test_every_model_has_an_example():
    assert {type(m) for m in ex.ALL} == set(MODELS)


@pytest.mark.parametrize("instance", ex.ALL, ids=lambda m: type(m).__name__)
def test_round_trip(instance: BaseModel):
    model = type(instance)
    as_json = instance.model_dump_json()
    assert model.model_validate_json(as_json) == instance
    assert model.model_validate(json.loads(as_json)) == instance


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_exported_schema_is_current(model: type[BaseModel]):
    path = SCHEMAS_DIR / f"{model.__name__}.json"
    assert path.read_text() == render(model), "run: uv run python -m contracts.export"


def test_invoice_schema_is_strict():
    assert INVOICE_JSON_SCHEMA["additionalProperties"] is False
    assert set(INVOICE_JSON_SCHEMA["required"]) == set(INVOICE_JSON_SCHEMA["properties"])


def test_invoice_is_types_only():
    # Formats are the Scorer's concern; only types and keys are enforced.
    data = ex.INVOICE.model_dump() | {"invoice_date": "Oct 1, 2026", "currency": "usd"}
    Invoice.model_validate(data)
    with pytest.raises(ValidationError):
        Invoice.model_validate(ex.INVOICE.model_dump() | {"total": "lots"})
    with pytest.raises(ValidationError):
        Invoice.model_validate(ex.INVOICE.model_dump() | {"notes": "extra"})
    with pytest.raises(ValidationError):
        Invoice.model_validate({k: v for k, v in ex.INVOICE.model_dump().items() if k != "tax"})


def test_internal_models_forbid_extra_fields():
    with pytest.raises(ValidationError):
        JobPosting.model_validate(ex.JOB_POSTING.model_dump() | {"surprise": 1})


def test_provider_facing_models_ignore_extra_fields():
    card = AgentCard.model_validate(ex.AGENT_CARD.model_dump() | {"skills": []})
    assert card == ex.AGENT_CARD
    result = ProviderResult.model_validate(ex.PROVIDER_RESULT.model_dump() | {"debug": "x"})
    assert result == ex.PROVIDER_RESULT


def test_provider_result_accepts_a_non_invoice_output():
    # It parses here; the Scorer marks the doc `invalid`.
    ProviderResult.model_validate(ex.PROVIDER_RESULT.model_dump() | {"output": {"foo": 1}})


def test_job_posting_allows_at_most_three_samples():
    with pytest.raises(ValidationError):
        JobPosting.model_validate(
            ex.JOB_POSTING.model_dump() | {"samples": [ex.SAMPLE_DOC.model_dump()] * 4}
        )


@pytest.mark.parametrize(
    "change",
    [
        {"total": 0.40},  # sums to 1.1
        {"total": -0.30, "line_items": 0.90},  # negative
    ],
)
def test_rubric_weights_validated(change):
    with pytest.raises(ValidationError):
        Rubric.model_validate(ex.RUBRIC.model_dump() | {"weights": ex.WEIGHTS | change})


def test_rubric_weights_need_all_six_fields():
    weights = dict(ex.WEIGHTS)
    weights.pop("currency")
    weights["total"] += 0.05
    with pytest.raises(ValidationError):
        Rubric.model_validate(ex.RUBRIC.model_dump() | {"weights": weights})


def test_doc_ids_are_opaque():
    assert len(new_doc_id()) == 12
    TrialDoc(doc_id=new_doc_id(), source="synthetic", answer=ex.INVOICE)
    with pytest.raises(ValidationError):
        TrialDoc(doc_id="trial-layout-3", source="synthetic", answer=ex.INVOICE)


def test_registered_provider_ids():
    assert ex.REGISTERED_PROVIDER.provider_id == "acme@1.0.0"
    assert ex.REGISTERED_PROVIDER.tasks_url == "http://localhost:8100/p/acme/tasks"


def test_submission_output_matches_status():
    timeout = dict(task_id="t", doc_id=ex.DOC_A, provider="acme@1.0.0", cost_usd=None)
    Submission(**timeout, status="timeout", output=None, latency_ms=60_000)
    Submission(**timeout, status="not_run", output=None, latency_ms=None)
    with pytest.raises(ValidationError):
        Submission(**timeout, status="ok", output=None, latency_ms=10)
    with pytest.raises(ValidationError):
        Submission(**timeout, status="error", output={"x": 1}, latency_ms=10)


def test_score_report_checks():
    with pytest.raises(ValidationError):
        ScoreReport.model_validate(ex.SCORE_REPORT.model_dump() | {"n_docs": 2})
    with pytest.raises(ValidationError):
        ScoreReport.model_validate(ex.SCORE_REPORT.model_dump() | {"ci_95": (0.99, 0.9)})


def _verdict(**change) -> Verdict:
    return Verdict.model_validate(ex.VERDICT.model_dump() | change)


def test_verdict_tie_recommends_the_tie_break_winner():
    v = _verdict(status="tie", tie=True, tied=["acme@1.0.0", "zeta@2"])
    assert v.recommended == "acme@1.0.0"


def test_verdict_insufficient_candidates():
    _verdict(
        status="insufficient_candidates",
        recommended=None,
        trial_accuracy=None,
        ci_95=None,
        cost_per_doc=None,
        valid_until=None,
    )


@pytest.mark.parametrize(
    "change",
    [
        {"tie": True},  # tie flag without tie status
        {"status": "tie", "tie": True},  # tie without tied list
        {"status": "tie", "tie": True, "tied": ["zeta@2", "beta@1"]},  # winner not in tied
        {"tied": ["acme@1.0.0", "zeta@2"]},  # tied list on a clear win
        {"recommended": None},  # recommended status, nobody recommended
        {"status": "insufficient_candidates"},  # insufficient, but recommends
    ],
)
def test_verdict_rejects_inconsistent_status(change):
    with pytest.raises(ValidationError):
        _verdict(**change)
