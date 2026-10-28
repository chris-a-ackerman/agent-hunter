"""The Rubric Builder (AGE-21, plan §3 #2, §8): turns a `JobSpec` into a `Rubric`.

It starts from the plan §8 default weights. One LLM call through the gateway may nudge them to
fit the job (e.g. "totals matter most" → more weight on `total`) and pick the required fields:

- each proposed weight is clamped to default ± `MAX_SHIFT` and ≥ 0, then all are renormalized
  to sum to 1; a field the model leaves out keeps its default;
- tolerances stay at the `Tolerances` defaults in the MVP (the model can't change them);
- invalid JSON, unknown field names or all-zero weights → the defaults, with a warning.

With no gateway (unit tests, LLM off) it returns the defaults. Rubrics are auto-approved (D6).
`build_rubric` satisfies `contracts.steps.BuildRubric`.
"""

import json
import logging
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, ValidationError

from contracts import Invoice, JobSpec, Rubric
from contracts.rubric import SCORED_FIELDS
from core.eval.defaults import DEFAULT_WEIGHTS
from gateway import DEFAULT_MODEL, Gateway

log = logging.getLogger(__name__)

COMPONENT = "rubric_builder"
MAX_TOKENS = 600  # gpt-6-luna spends tokens on reasoning before answering
MAX_SHIFT = 0.10
DEFAULT_REQUIRED_FIELDS: tuple[str, ...] = ("vendor_name", "invoice_date", "currency", "total")
INVOICE_FIELDS = frozenset(Invoice.model_fields)

SYSTEM_PROMPT = f"""\
You are the Rubric Builder of a hiring service for AI agents. Agents extract invoices to JSON \
and we score each output field by field. Given a job and the default weights of the scored \
fields, propose weights that fit what this requester cares about. Stay close to the defaults: \
each weight may move by at most {MAX_SHIFT:.2f}. Also list the fields an output must contain to \
be usable.

Reply with a JSON object only:
{{"weights": {{"<field>": <number>, ...}}, "required_fields": ["<field>", ...], \
"justification": "<one sentence>"}}
Scored fields: {", ".join(SCORED_FIELDS)}. Required fields must be invoice fields: \
{", ".join(sorted(INVOICE_FIELDS))}."""


class _Proposal(BaseModel):
    """What the model must return."""

    weights: dict[str, float]
    required_fields: list[str]
    justification: str = ""


class _Rejected(ValueError):
    """The model's proposal can't be used; the message says why."""


def _rubric(spec: JobSpec, weights: dict[str, float], required: list[str]) -> Rubric:
    return Rubric(
        rubric_id=f"rubric-{uuid.uuid4().hex[:12]}",
        job_id=spec.job_id,
        weights=weights,
        required_fields=required,
        approved_by="auto",
        approved_at=datetime.now(UTC),
    )


def default_rubric(spec: JobSpec) -> Rubric:
    """Plan §8 default weights and the default required fields."""
    return _rubric(spec, dict(DEFAULT_WEIGHTS), list(DEFAULT_REQUIRED_FIELDS))


def bound_weights(proposed: dict[str, float]) -> dict[str, float]:
    """Clamp each weight to default ± MAX_SHIFT and ≥ 0, fill missing fields with their
    default, and renormalize to sum to 1. Raises `_Rejected` on unknown fields or all zeros."""
    unknown = sorted(set(proposed) - set(SCORED_FIELDS))
    if unknown:
        raise _Rejected(f"unknown weight fields {unknown}")
    # Checked before clamping, which would otherwise lift zeros back to default - MAX_SHIFT.
    if proposed and all(w <= 0 for w in proposed.values()):
        raise _Rejected("all weights are zero")
    clamped = {}
    for field, default in DEFAULT_WEIGHTS.items():
        w = proposed.get(field, default)
        clamped[field] = max(0.0, default - MAX_SHIFT, min(default + MAX_SHIFT, w))
    total = sum(clamped.values())  # > 0: total and line_items never clamp below 0.20
    return {field: w / total for field, w in clamped.items()}


def _check_required(fields: list[str]) -> list[str]:
    unknown = sorted(set(fields) - INVOICE_FIELDS)
    if unknown:
        raise _Rejected(f"unknown required fields {unknown}")
    if not fields:
        raise _Rejected("no required fields")
    return list(dict.fromkeys(fields))  # drop duplicates, keep order


def _user_message(spec: JobSpec) -> str:
    # JobSpec carries the reviewer's summary, not the posting's full description.
    job = "\n".join(part for part in (spec.reason, spec.notes) if part)
    return (
        f"Job:\n{job or '(no description)'}\n\n"
        f"Default weights:\n{json.dumps(DEFAULT_WEIGHTS)}\n\n"
        f"Default required fields:\n{json.dumps(list(DEFAULT_REQUIRED_FIELDS))}"
    )


def build_rubric(spec: JobSpec, *, gateway: Gateway | None = None) -> Rubric:
    """The rubric for `spec`: the defaults, adjusted within bounds by the LLM if `gateway` is
    given. Never raises on bad model output; it falls back to the defaults instead."""
    if gateway is None:
        return default_rubric(spec)

    resp = gateway.call(
        COMPONENT,
        DEFAULT_MODEL,
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_message(spec)},
        ],
        response_format={"type": "json_object"},
        job_id=spec.job_id,
        max_tokens=MAX_TOKENS,
    )
    try:
        if resp.parsed is None:
            raise _Rejected("not a JSON object")
        try:
            proposal = _Proposal.model_validate(resp.parsed)
        except ValidationError as e:
            raise _Rejected(f"wrong shape ({e.error_count()} errors)") from None
        weights = bound_weights(proposal.weights)
        required = _check_required(proposal.required_fields)
    except _Rejected as e:
        log.warning(
            "Rubric Builder: unusable model output for job %s (%s); using defaults", spec.job_id, e
        )
        return default_rubric(spec)

    log.info("Rubric Builder: job %s weights adjusted: %s", spec.job_id, proposal.justification)
    return _rubric(spec, weights, required)
