"""Trivial stubs for every step, type-checked against contracts.steps by test_steps.py."""

from contracts import (
    JobPosting,
    JobSpec,
    RegisteredProvider,
    Rubric,
    ScoreReport,
    ScoringConfig,
    Submission,
    TrialPackage,
    Verdict,
    steps,
)
from contracts.steps import OnEvent
from tests.test_contracts import examples as ex


def review_job(posting: JobPosting) -> JobSpec:
    return ex.JOB_SPEC


def build_rubric(spec: JobSpec) -> Rubric:
    return ex.RUBRIC


def build_trial(spec: JobSpec, posting: JobPosting) -> TrialPackage:
    return ex.TRIAL_PACKAGE


def build_eval(rubric: Rubric, trial: TrialPackage) -> ScoringConfig:
    return ex.SCORING_CONFIG


def find_candidates(spec: JobSpec, registry: list[RegisteredProvider]) -> list[RegisteredProvider]:
    return registry


async def run_trial(
    trial: TrialPackage,
    providers: list[RegisteredProvider],
    requester_id: str,
    on_event: OnEvent | None = None,
) -> list[Submission]:
    return [ex.SUBMISSION]


def score(config: ScoringConfig, submissions: list[Submission]) -> list[ScoreReport]:
    return [ex.SCORE_REPORT]


def rank(reports: list[ScoreReport], submissions: list[Submission], trial_id: str) -> Verdict:
    return ex.VERDICT.model_copy(update={"evidence": trial_id})


_review: steps.ReviewJob = review_job
_rubric: steps.BuildRubric = build_rubric
_trial: steps.BuildTrial = build_trial
_eval: steps.BuildEval = build_eval
_find: steps.FindCandidates = find_candidates
_run: steps.RunTrial = run_trial
_score: steps.Score = score
_rank: steps.Rank = rank
