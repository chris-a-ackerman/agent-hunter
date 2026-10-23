"""The `Store` interface: persistent job state for the pipeline (plan §5.2, D21).

The pipeline only ever talks to a `Store`; the MVP implementation is `JsonStore` (local files),
a Supabase implementation follows after the MVP (AGE-30).
"""

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from contracts import (
    JobPosting,
    JobSpec,
    JobStatus,
    RegisteredProvider,
    Rubric,
    ScoreReport,
    Submission,
    TrialPackage,
    Verdict,
)

S = JobStatus

# Plan §5.2 status machine, plus running <-> paused (D18) and needs_info -> reviewing once the
# requester answers the Job Reviewer's questions. Statuses missing as keys are final.
TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    S.SUBMITTED: frozenset({S.REVIEWING}),
    S.REVIEWING: frozenset({S.NEEDS_INFO, S.REJECTED, S.RUBRIC_READY}),
    S.NEEDS_INFO: frozenset({S.REVIEWING}),
    S.RUBRIC_READY: frozenset({S.TRIAL_BUILT}),
    S.TRIAL_BUILT: frozenset({S.RUNNING}),
    S.RUNNING: frozenset({S.SCORED, S.PAUSED}),
    S.PAUSED: frozenset({S.RUNNING}),
    S.SCORED: frozenset({S.RANKED}),
    S.RANKED: frozenset({S.HIRED}),
    S.HIRED: frozenset({S.OUTCOME_REPORTED}),
}


class StoreError(Exception):
    """Base class for store errors."""


class JobNotFound(StoreError, KeyError):
    """No job with that ID."""


class IllegalTransition(StoreError, ValueError):
    """`set_status` was asked for a move the status machine doesn't allow."""

    def __init__(self, job_id: str, current: JobStatus, target: JobStatus) -> None:
        super().__init__(f"job {job_id}: illegal status transition {current} -> {target}")
        self.job_id, self.current, self.target = job_id, current, target


def check_transition(job_id: str, current: JobStatus, target: JobStatus) -> None:
    if target not in TRANSITIONS.get(current, frozenset()):
        raise IllegalTransition(job_id, current, target)


def _now() -> datetime:
    return datetime.now(UTC)


class StatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: JobStatus
    at: datetime = Field(default_factory=_now)
    note: str | None = None


class StepTiming(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: str
    started_at: datetime
    finished_at: datetime


class JobRecord(BaseModel):
    """Everything stored about one job. Local to `core/store`, not a contract."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    created_at: datetime = Field(default_factory=_now)
    posting: JobPosting
    status: JobStatus = JobStatus.SUBMITTED
    status_history: list[StatusChange] = Field(default_factory=list)
    spec: JobSpec | None = None
    rubric: Rubric | None = None
    trial: TrialPackage | None = None
    submissions: list[Submission] = Field(default_factory=list)
    scores: list[ScoreReport] = Field(default_factory=list)
    verdict: Verdict | None = None
    timings: list[StepTiming] = Field(default_factory=list)


class Store(Protocol):
    """Job state. Each `save_*` replaces what was stored for that job before."""

    def create_job(self, posting: JobPosting) -> str: ...

    def get_job(self, job_id: str) -> JobRecord: ...

    def set_status(self, job_id: str, status: JobStatus, note: str | None = None) -> None: ...

    def save_job_spec(self, job_id: str, spec: JobSpec) -> None: ...

    def save_rubric(self, job_id: str, rubric: Rubric) -> None: ...

    def save_trial(self, job_id: str, trial: TrialPackage) -> None: ...

    def save_submissions(self, job_id: str, subs: list[Submission]) -> None: ...

    def save_scores(self, job_id: str, reports: list[ScoreReport]) -> None: ...

    def save_verdict(self, job_id: str, verdict: Verdict) -> None: ...

    def record_step(
        self, job_id: str, step: str, started_at: datetime, finished_at: datetime
    ) -> None: ...

    def upsert_provider(self, p: RegisteredProvider) -> None: ...

    def list_providers(self) -> list[RegisteredProvider]: ...

    def platform_spend(self, job_id: str) -> float: ...
