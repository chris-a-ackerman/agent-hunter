"""Pipeline → live view events, and the pause signal (D18)."""

from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field

from contracts.base import Contract

if TYPE_CHECKING:
    from contracts.provider import Submission


class RunEvent(Contract):
    job_id: str
    step: str
    kind: Literal["started", "finished", "task_done", "provider_done", "paused", "stopped"]
    detail: dict[str, Any] = Field(default_factory=dict)


class RunPaused(Exception):
    """Raised by run_trial when a provider returns 402 budget_exhausted (D18).

    Carries every submission so far; unsent tasks are already marked `not_run`.
    """

    def __init__(self, submissions: "list[Submission]", reason: str = "budget_exhausted"):
        super().__init__(reason)
        self.submissions = submissions
        self.reason = reason
