import logging

import pytest

from contracts import JobPosting, JobSpec, Rubric, Tolerances
from contracts.rubric import SCORED_FIELDS
from core.agents.job_reviewer import review_job
from core.agents.rubric_builder import (
    COMPONENT,
    DEFAULT_REQUIRED_FIELDS,
    MAX_SHIFT,
    bound_weights,
    build_rubric,
)
from core.eval.defaults import DEFAULT_WEIGHTS
from examples.make_examples import EXAMPLES_DIR
from gateway import DEFAULT_MODEL
from mocks.fake_gateway import FakeGateway

APPROVE = {"decision": "approve", "reason": "Invoice extraction to an invoice schema."}
TOTALS_FIRST = {
    "weights": {
        "total": 0.40,
        "line_items": 0.25,
        "vendor_name": 0.15,
        "invoice_date": 0.10,
        "invoice_number": 0.05,
        "currency": 0.05,
    },
    "required_fields": ["vendor_name", "total"],
    "justification": "The requester says totals matter most.",
}


def spec_for(name: str) -> JobSpec:
    posting = JobPosting.model_validate_json((EXAMPLES_DIR / f"{name}.json").read_text())
    spec = review_job(posting, gateway=FakeGateway(APPROVE), samples_dir=EXAMPLES_DIR)  # type: ignore[arg-type]
    return spec.model_copy(update={"job_id": f"job-{name}"})


def build(spec: JobSpec, *replies) -> tuple[Rubric, FakeGateway]:
    gw = FakeGateway(*replies)
    return build_rubric(spec, gateway=gw), gw  # type: ignore[arg-type]


def assert_defaults(rubric: Rubric) -> None:
    assert rubric.weights == DEFAULT_WEIGHTS
    assert rubric.required_fields == list(DEFAULT_REQUIRED_FIELDS)


# --- acceptance: both invoice examples --------------------------------------------------------


@pytest.mark.parametrize("name", ["invoice_clean", "invoice_long"])
def test_valid_rubric_for_invoice_examples(name):
    spec = spec_for(name)
    rubric, gw = build(spec, TOTALS_FIRST)
    Rubric.model_validate(rubric.model_dump())  # round-trips through the contract
    assert rubric.job_id == spec.job_id
    assert set(rubric.weights) == set(SCORED_FIELDS)
    assert sum(rubric.weights.values()) == pytest.approx(1.0)
    assert rubric.approved_by == "auto"
    assert rubric.tolerances == Tolerances()
    [call] = gw.calls
    assert call["component"] == COMPONENT and call["model"] == DEFAULT_MODEL
    assert call["response_format"] == {"type": "json_object"} and call["max_tokens"]


@pytest.mark.parametrize("name", ["invoice_clean", "invoice_long"])
def test_no_gateway_returns_defaults(name):
    rubric = build_rubric(spec_for(name))
    assert_defaults(rubric)
    assert rubric.approved_by == "auto"


def test_llm_adjustment_is_applied():
    rubric, _ = build(spec_for("invoice_clean"), TOTALS_FIRST)
    assert rubric.weights["total"] > DEFAULT_WEIGHTS["total"]
    assert rubric.weights == pytest.approx(TOTALS_FIRST["weights"])  # already sums to 1
    assert rubric.required_fields == ["vendor_name", "total"]


def test_prompt_carries_spec_text_and_defaults():
    spec = spec_for("invoice_clean").model_copy(update={"notes": "Totals matter most."})
    _, gw = build(spec, TOTALS_FIRST)
    user = gw.calls[0]["messages"][-1]["content"]
    assert "Totals matter most." in user and spec.reason in user
    assert '"total": 0.3' in user


# --- acceptance: invalid output falls back to the defaults, with a warning ------------------


@pytest.mark.parametrize(
    "reply",
    [
        "not json",
        {"weights": "heavy", "required_fields": []},
        {"required_fields": ["total"]},  # no weights
        {"weights": {"totl": 0.5}, "required_fields": ["total"]},  # unknown weight field
        {"weights": DEFAULT_WEIGHTS, "required_fields": ["colour"]},  # unknown required field
        {"weights": DEFAULT_WEIGHTS, "required_fields": []},
        {"weights": dict.fromkeys(SCORED_FIELDS, 0.0), "required_fields": ["total"]},
    ],
    ids=[
        "not-json",
        "bad-shape",
        "no-weights",
        "unknown-weight",
        "unknown-required",
        "no-required",
        "all-zero",
    ],
)
def test_invalid_output_falls_back_with_warning(reply, caplog):
    with caplog.at_level(logging.WARNING, logger="core.agents.rubric_builder"):
        rubric, _ = build(spec_for("invoice_clean"), reply)
    assert_defaults(rubric)
    assert "using defaults" in caplog.text


# --- bounds -----------------------------------------------------------------------------------


def test_weights_clamped_to_default_plus_minus_shift_then_renormalized():
    w = bound_weights({"total": 0.95, "line_items": 0.0})
    # total clamps to 0.40, line_items to 0.20, the rest keep their defaults: sum 1.00
    assert w["total"] == pytest.approx(DEFAULT_WEIGHTS["total"] + MAX_SHIFT)
    assert w["line_items"] == pytest.approx(DEFAULT_WEIGHTS["line_items"] - MAX_SHIFT)
    assert sum(w.values()) == pytest.approx(1.0)


def test_weights_never_negative_and_renormalized():
    w = bound_weights({"invoice_number": -1.0, "currency": -1.0, "total": 0.40})
    assert w["invoice_number"] == 0.0 and w["currency"] == 0.0
    assert all(v >= 0 for v in w.values())
    assert sum(w.values()) == pytest.approx(1.0)


def test_missing_weights_keep_defaults():
    assert bound_weights({}) == pytest.approx(DEFAULT_WEIGHTS)


def test_wild_proposal_stays_valid_rubric():
    wild = {"weights": dict.fromkeys(SCORED_FIELDS, 5.0), "required_fields": ["total", "total"]}
    rubric, _ = build(spec_for("invoice_long"), wild)
    assert sum(rubric.weights.values()) == pytest.approx(1.0)
    for f, d in DEFAULT_WEIGHTS.items():  # each clamped to d + 0.10, then scaled down
        assert rubric.weights[f] == pytest.approx((d + MAX_SHIFT) / 1.6)
    assert rubric.required_fields == ["total"]
