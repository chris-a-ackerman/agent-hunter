from datetime import date
from pathlib import Path

import pytest

from contracts import JobPosting
from contracts.rubric import SCORED_FIELDS
from core.agents.job_reviewer import COMPONENT, review_job
from examples.make_examples import EXAMPLES_DIR
from gateway import DEFAULT_MODEL, Gateway, JsonlCostSink
from mocks.fake_gateway import FakeGateway

APPROVE = {"decision": "approve", "reason": "Invoice extraction to an invoice schema."}
REJECT = {"decision": "reject", "reason": "A cover letter has no single correct answer."}


def load(name: str) -> JobPosting:
    return JobPosting.model_validate_json((EXAMPLES_DIR / f"{name}.json").read_text())


def review(posting: JobPosting, gw: FakeGateway):
    return review_job(posting, gateway=gw, samples_dir=EXAMPLES_DIR)  # type: ignore[arg-type]


# --- acceptance: the three examples ---------------------------------------------------------


@pytest.mark.parametrize("name", ["invoice_clean", "invoice_long"])
def test_invoice_examples_are_approved_with_job_type_fields_and_budget(name):
    gw = FakeGateway(APPROVE)
    spec = review(load(name), gw)
    assert spec.decision == "approve"
    assert spec.job_type == "invoice_extraction"
    assert spec.fields == list(SCORED_FIELDS)
    assert spec.per_doc_budget_usd == pytest.approx(5.0 / 15)
    assert spec.questions == []
    [call] = gw.calls
    assert call["component"] == COMPONENT and call["model"] == DEFAULT_MODEL
    assert call["response_format"] == {"type": "json_object"} and call["max_tokens"]


def test_unscorable_example_is_rejected_with_the_models_reason():
    spec = review(load("reject_unscorable"), FakeGateway(REJECT))
    assert spec.decision == "reject"
    assert spec.reason == REJECT["reason"]
    assert spec.job_type is None and spec.fields == []


def test_missing_output_schema_is_needs_info_without_a_model_call():
    gw = FakeGateway()
    posting = load("invoice_clean").model_copy(update={"output_schema": None})
    spec = review(posting, gw)
    assert spec.decision == "needs_info"
    assert "output schema" in spec.questions[0]
    assert gw.calls == []


# --- pre-checks ------------------------------------------------------------------------------


def test_invalid_json_schema_is_needs_info():
    posting = load("invoice_clean").model_copy(update={"output_schema": {"type": 42}})
    spec = review(posting, FakeGateway())
    assert spec.decision == "needs_info" and "valid JSON Schema" in spec.reason


def test_negative_budget_is_needs_info():
    posting = load("invoice_clean").model_copy(update={"trial_budget_usd": -1.0})
    spec = review(posting, FakeGateway())
    assert spec.decision == "needs_info" and "budget" in spec.reason


def test_open_deadline_must_be_acknowledged():
    posting = load("invoice_clean").model_copy(update={"deadline_open_ack": False})
    assert review(posting, FakeGateway()).decision == "needs_info"
    dated = posting.model_copy(update={"deadline": date(2026, 11, 1)})
    assert review(dated, FakeGateway(APPROVE)).decision == "approve"


def test_missing_sample_file_is_needs_info_naming_the_sample():
    posting = load("invoice_clean")
    spec = review_job(posting, gateway=FakeGateway(), samples_dir=Path("/nonexistent"))  # type: ignore[arg-type]
    assert spec.decision == "needs_info"
    assert "Sample 1" in spec.reason and posting.samples[0].path in spec.reason


def test_invalid_sample_answer_is_needs_info_naming_the_sample():
    posting = load("invoice_clean")
    bad = posting.samples[1].answer.model_construct(vendor_name=7)  # bypasses validation
    samples = [posting.samples[0], posting.samples[1].model_copy(update={"answer": bad})]
    spec = review(posting.model_copy(update={"samples": samples}), FakeGateway())
    assert spec.decision == "needs_info" and "Sample 2" in spec.reason


# --- LLM step --------------------------------------------------------------------------------


def test_needs_info_passes_the_models_questions_through():
    reply = {"decision": "needs_info", "reason": "Unclear.", "questions": ["Which currency?"]}
    spec = review(load("invoice_clean"), FakeGateway(reply))
    assert spec.decision == "needs_info" and spec.questions == ["Which currency?"]


def test_invalid_json_is_retried_once():
    gw = FakeGateway("not json", APPROVE)
    assert review(load("invoice_clean"), gw).decision == "approve"
    assert len(gw.calls) == 2


@pytest.mark.parametrize("second", ["still not json", {"decision": "maybe", "reason": "x"}])
def test_two_bad_replies_give_needs_info_review_failed_never_approve(second):
    gw = FakeGateway("not json", second)
    spec = review(load("invoice_clean"), gw)
    assert spec.decision == "needs_info" and spec.reason.startswith("review failed")
    assert len(gw.calls) == 2


def test_approve_without_the_scored_fields_is_downgraded_to_needs_info():
    posting = load("invoice_clean").model_copy(update={"output_schema": {"type": "object"}})
    spec = review(posting, FakeGateway(APPROVE))
    assert spec.decision == "needs_info" and "total" in spec.reason


# --- live ------------------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.parametrize(
    ("name", "decision"),
    [("invoice_clean", "approve"), ("invoice_long", "approve"), ("reject_unscorable", "reject")],
)
def test_live_examples(name, decision, tmp_path):
    gw = Gateway(JsonlCostSink(tmp_path / "llm_calls.jsonl"), account="live-test")
    spec = review_job(load(name), gateway=gw, samples_dir=EXAMPLES_DIR)
    assert spec.decision == decision, spec.reason
