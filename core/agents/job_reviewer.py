"""The Job Reviewer (AGE-13, plan §3 #1): the first agent a job meets.

It checks that a posting is complete and automatically scorable and turns it into a `JobSpec`:
`approve`, `needs_info` (with specific questions) or `reject` (with a reason).

1. Deterministic pre-checks, no model call: an output schema that is a valid JSON Schema,
   non-negative budgets, a deadline or an acknowledged open deadline, and samples whose files
   exist and whose answers are valid `Invoice`s. Any failure → `needs_info`.
2. One LLM call through the gateway decides whether the outputs can be scored automatically
   against a known correct answer. Invalid JSON gets one retry, then `needs_info` ("review
   failed"): a broken review never approves a job.

`review_job` satisfies `contracts.steps.ReviewJob` once the pipeline binds `gateway` and
`samples_dir` (see `core.pipeline.config.build_steps`).
"""

import functools
import json
import os
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, Field, ValidationError

from contracts import Invoice, JobPosting, JobSpec
from contracts.rubric import SCORED_FIELDS
from core.store import JsonStore
from gateway import DEFAULT_MODEL, Gateway, JsonlCostSink

COMPONENT = "job_reviewer"
JOB_TYPE = "invoice_extraction"
MAX_TOKENS = 400
MAX_ATTEMPTS = 2  # the first call plus one retry on invalid JSON

SYSTEM_PROMPT = f"""\
You are the Job Reviewer of a hiring service for AI agents. A requester posts a job; we hire an \
agent for it only if every output can be scored AUTOMATICALLY against a known correct answer: \
structured fields with objectively right or wrong values.

The only job family we accept is invoice extraction: reading invoice documents and returning \
JSON that matches an invoice schema (vendor, invoice number, date, currency, line items, totals).

Decide:
- "approve": the job is invoice extraction to an invoice-shaped JSON schema.
- "reject": the outputs are subjective, creative or open-ended (no single right answer), or the \
job is not invoice extraction. Give a one-sentence reason a requester would understand.
- "needs_info": it might be invoice extraction but the posting is ambiguous. Ask 1 to 3 \
specific questions.

Reply with a JSON object only:
{{"decision": "approve" | "needs_info" | "reject", "reason": "<one sentence>", \
"questions": ["<question>", ...]}}
"questions" is empty unless the decision is "needs_info". The scored invoice fields are: \
{", ".join(SCORED_FIELDS)}."""


class _Review(BaseModel):
    """What the model must return."""

    decision: Literal["approve", "needs_info", "reject"]
    reason: str = Field(min_length=1)
    questions: list[str] = Field(default_factory=list, max_length=3)


@functools.cache
def default_gateway() -> Gateway:
    """A gateway logging to the job store's cost log (`STORE_DIR/llm_calls.jsonl`)."""
    return Gateway(JsonlCostSink(JsonStore().llm_calls_path))


def _spec(posting: JobPosting, decision: str, reason: str, **extra: Any) -> JobSpec:
    return JobSpec(
        job_id="unassigned",  # ReviewJob doesn't get the job ID; run_pipeline sets it
        decision=decision,  # type: ignore[arg-type]
        reason=reason,
        n_real_docs=posting.n_real_docs,
        per_doc_budget_usd=posting.job_budget_usd / posting.n_real_docs,
        region=posting.region,
        **extra,
    )


def _needs_info(posting: JobPosting, reason: str, questions: list[str]) -> JobSpec:
    return _spec(posting, "needs_info", reason, questions=questions)


def pre_check(posting: JobPosting, samples_dir: Path) -> JobSpec | None:
    """The deterministic checks. Returns a `needs_info` spec on the first failure, else None."""
    if posting.output_schema is None:
        return _needs_info(
            posting,
            "The posting has no output schema.",
            ["Please provide the output schema: the JSON Schema each output must match."],
        )
    try:
        Draft202012Validator.check_schema(posting.output_schema)
    except SchemaError as e:
        return _needs_info(
            posting,
            f"The output schema is not a valid JSON Schema: {e.message}",
            ["Please provide the output schema as a valid JSON Schema."],
        )
    if posting.job_budget_usd < 0 or posting.trial_budget_usd < 0:
        return _needs_info(
            posting,
            "The job and trial budgets must not be negative.",
            ["What are the job budget and the trial budget, in USD?"],
        )
    if posting.deadline is None and not posting.deadline_open_ack:
        return _needs_info(
            posting,
            "The posting has no deadline and doesn't acknowledge an open-ended one.",
            ["What is the deadline? If there is none, please confirm the job is open-ended."],
        )
    for i, sample in enumerate(posting.samples, start=1):
        name = f"Sample {i} ({sample.path})"
        if not (samples_dir / sample.path).is_file():
            return _needs_info(
                posting,
                f"{name} was not found.",
                [f"Please re-upload {name.lower()}; the file is missing."],
            )
        if sample.answer is not None:
            try:
                Invoice.model_validate(sample.answer.model_dump(warnings=False))
            except ValidationError:
                return _needs_info(
                    posting,
                    f"{name} has an answer that doesn't match the invoice schema.",
                    [f"Please correct the answer for {name.lower()}."],
                )
    return None


def _user_message(posting: JobPosting) -> str:
    return (
        f"Job description:\n{posting.description}\n\n"
        f"Output description:\n{posting.output_description}\n\n"
        f"Output schema (JSON Schema):\n{json.dumps(posting.output_schema)}"
    )


def _ask_model(gateway: Gateway, posting: JobPosting) -> _Review | None:
    """The model's review, or None if it returned invalid JSON on every attempt."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _user_message(posting)},
    ]
    for _ in range(MAX_ATTEMPTS):
        resp = gateway.call(
            COMPONENT,
            DEFAULT_MODEL,
            messages,
            response_format={"type": "json_object"},
            max_tokens=MAX_TOKENS,
        )
        if resp.parsed is None:
            continue
        try:
            return _Review.model_validate(resp.parsed)
        except ValidationError:
            continue
    return None


def _invoice_fields_missing(posting: JobPosting) -> list[str]:
    props = (posting.output_schema or {}).get("properties")
    return sorted(set(SCORED_FIELDS) - set(props if isinstance(props, dict) else {}))


def review_job(
    posting: JobPosting,
    *,
    gateway: Gateway | None = None,
    samples_dir: Path | None = None,
) -> JobSpec:
    """Review `posting`. Sample paths resolve against `samples_dir` (the directory the posting
    file lives in; default `SAMPLES_DIR`, else `examples`)."""
    samples_dir = samples_dir or Path(os.environ.get("SAMPLES_DIR") or "examples")
    failed = pre_check(posting, samples_dir)
    if failed is not None:
        return failed

    review = _ask_model(gateway or default_gateway(), posting)
    if review is None:
        return _needs_info(
            posting,
            "review failed: the reviewer returned no valid answer.",
            ["Please resubmit the job; if this persists, contact us."],
        )
    if review.decision == "reject":
        return _spec(posting, "reject", review.reason)
    if review.decision == "needs_info":
        questions = review.questions or ["Please clarify what each output should contain."]
        return _needs_info(posting, review.reason, questions)

    # Approve only if the schema really carries the fields we score (plan §8).
    missing = _invoice_fields_missing(posting)
    if missing:
        return _needs_info(
            posting,
            f"The output schema lacks the scored invoice fields: {', '.join(missing)}.",
            [f"Can the output schema include {', '.join(missing)}?"],
        )
    return _spec(posting, "approve", review.reason, job_type=JOB_TYPE, fields=list(SCORED_FIELDS))
