"""Job intake: what the requester posts and what the Job Reviewer makes of it."""

from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from contracts.base import Contract, UsdAmount
from contracts.invoice import Invoice


class JobStatus(StrEnum):
    SUBMITTED = "submitted"
    REVIEWING = "reviewing"
    NEEDS_INFO = "needs_info"
    REJECTED = "rejected"
    RUBRIC_READY = "rubric_ready"
    TRIAL_BUILT = "trial_built"
    RUNNING = "running"
    PAUSED = "paused"  # D18: a provider's model account ran out of credit
    SCORED = "scored"
    RANKED = "ranked"
    HIRED = "hired"
    OUTCOME_REPORTED = "outcome_reported"


class SampleDoc(Contract):
    path: str = Field(description="Path to the sample document, relative to the posting file")
    answer: Invoice | None = None


class JobPosting(Contract):
    requester_id: str
    description: str
    output_description: str
    output_schema: dict[str, Any] | None = Field(
        default=None, description="Missing → the Job Reviewer returns needs_info"
    )
    samples: list[SampleDoc] = Field(default_factory=list, max_length=3)
    n_real_docs: int = Field(default=15, ge=1, description="Size of the real job batch")
    job_budget_usd: UsdAmount
    trial_budget_usd: UsdAmount
    deadline: date | None = None
    deadline_open_ack: bool = False
    region: str | None = None
    min_reputation: float | None = Field(default=None, description="Unused in the MVP")
    allow_unrated: bool = True
    sourcing_mode: str = "registry"


class JobSpec(Contract):
    job_id: str
    decision: Literal["approve", "needs_info", "reject"]
    reason: str
    questions: list[str] = Field(default_factory=list)
    job_type: str | None = Field(default=None, description='"invoice_extraction" when approved')
    fields: list[str] = Field(default_factory=list)
    n_real_docs: int = Field(ge=1)
    per_doc_budget_usd: UsdAmount = Field(description="job_budget_usd / n_real_docs")
    region: str | None = None
    notes: str = ""
