"""Step interfaces. The pipeline depends on these, so it and the mocks can be written before
the real steps exist. Any plain function with the matching signature satisfies the Protocol.
"""

from collections.abc import Callable
from typing import Protocol

from contracts.events import RunEvent
from contracts.job import JobPosting, JobSpec
from contracts.provider import RegisteredProvider, Submission
from contracts.rubric import Rubric
from contracts.scoring import ScoreReport, ScoringConfig
from contracts.trial import TrialPackage
from contracts.verdict import Verdict

OnEvent = Callable[[RunEvent], None]


class ReviewJob(Protocol):  # AGE-13
    def __call__(self, posting: JobPosting) -> JobSpec: ...


class BuildRubric(Protocol):  # AGE-21
    def __call__(self, spec: JobSpec) -> Rubric: ...


class BuildTrial(Protocol):  # AGE-22
    def __call__(self, spec: JobSpec, posting: JobPosting) -> TrialPackage: ...


class BuildEval(Protocol):  # AGE-23
    def __call__(self, rubric: Rubric, trial: TrialPackage) -> ScoringConfig: ...


class FindCandidates(Protocol):  # AGE-14
    def __call__(
        self, spec: JobSpec, registry: list[RegisteredProvider]
    ) -> list[RegisteredProvider]: ...


class RunTrial(Protocol):  # AGE-15
    """Raises contracts.RunPaused on a provider's 402 budget_exhausted (D18)."""

    async def __call__(
        self,
        trial: TrialPackage,
        providers: list[RegisteredProvider],
        requester_id: str,
        on_event: OnEvent | None = None,
    ) -> list[Submission]: ...


class Score(Protocol):  # AGE-19 (real) / AGE-16 (mock)
    def __call__(
        self, config: ScoringConfig, submissions: list[Submission]
    ) -> list[ScoreReport]: ...


class Rank(Protocol):  # AGE-24
    def __call__(
        self, reports: list[ScoreReport], submissions: list[Submission], trial_id: str
    ) -> Verdict: ...
