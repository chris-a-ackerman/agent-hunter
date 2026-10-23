"""The pipeline runner (AGE-9, plan §4.1, §5.2): one job through every step, in order.

Job Reviewer → Rubric Builder → Trial Builder → Eval Builder → Candidate Finder → Trial Runner
→ Scorer → Ranker. Each output is saved to the `Store`, the job status advances after each
stage, each step's timing is recorded and `RunEvent(started/finished)` is emitted around it.

The steps are plain callables behind `contracts.steps`, bundled in `Steps`, so mocks and stubs
can stand in. Synchronous steps run in a worker thread, so a slow (LLM) step doesn't block the
API's event loop.
"""

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from contracts import JobPosting, JobStatus, RegisteredProvider, RunEvent, RunPaused, steps
from contracts.steps import OnEvent
from core.store import Store

log = logging.getLogger(__name__)

S = JobStatus


@dataclass(frozen=True)
class Steps:
    review: steps.ReviewJob
    rubric: steps.BuildRubric
    trial: steps.BuildTrial
    eval: steps.BuildEval
    candidates: steps.FindCandidates
    run_trial: steps.RunTrial
    score: steps.Score
    rank: steps.Rank
    # The provider registry the Candidate Finder filters (D22). Called once per run; sync or
    # async (`core.runner.registry.refresh_registry`).
    registry: Callable[[], list[RegisteredProvider] | Awaitable[list[RegisteredProvider]]]


class _Run:
    """State for one run: the job, the store and the event callback."""

    def __init__(self, job_id: str, store: Store, on_event: OnEvent | None) -> None:
        self.job_id, self.store, self.on_event = job_id, store, on_event

    def emit(self, step: str, kind: Any, **detail: Any) -> None:
        if self.on_event is not None:
            self.on_event(RunEvent(job_id=self.job_id, step=step, kind=kind, detail=detail))

    async def step(self, name: str, fn: Callable[..., Any], *args: Any) -> Any:
        self.emit(name, "started")
        started = datetime.now(UTC)
        # A sync step runs in the thread; an async one (any callable returning an awaitable)
        # only builds its coroutine there, which is then awaited on this loop.
        result = await asyncio.to_thread(fn, *args)
        if inspect.isawaitable(result):
            result = await result
        finished = datetime.now(UTC)
        self.store.record_step(self.job_id, name, started, finished)
        ms = round((finished - started).total_seconds() * 1000)
        self.emit(name, "finished", duration_ms=ms)
        return result


async def run_pipeline(
    posting: JobPosting,
    *,
    store: Store,
    steps: Steps,
    on_event: OnEvent | None = None,
    job_id: str | None = None,
) -> str:
    """Run one job to `ranked` (or `rejected` / `needs_info` / `paused`) and return its ID.

    `job_id` resumes a job the caller already created with `store.create_job` (the API does,
    so it can return the ID at once); otherwise a new job is created here. Any exception other
    than `RunPaused` is logged, emitted as `stopped` and re-raised; the job keeps its last status.
    """
    if job_id is None:
        job_id = store.create_job(posting)
    run = _Run(job_id, store, on_event)
    current = "review"
    try:
        store.set_status(job_id, S.REVIEWING)
        spec = await run.step(current, steps.review, posting)
        # ReviewJob only sees the posting, so the spec gets the job's ID here; every later
        # output (rubric, trial) inherits it from the spec.
        spec = spec.model_copy(update={"job_id": job_id})
        store.save_job_spec(job_id, spec)
        if spec.decision == "reject":
            store.set_status(job_id, S.REJECTED, note=spec.reason)
            return job_id
        if spec.decision == "needs_info":
            store.set_status(job_id, S.NEEDS_INFO, note=spec.reason)
            return job_id

        current = "rubric"
        rubric = await run.step(current, steps.rubric, spec)
        store.save_rubric(job_id, rubric)
        store.set_status(job_id, S.RUBRIC_READY)

        current = "trial"
        trial = await run.step(current, steps.trial, spec, posting)
        store.save_trial(job_id, trial)
        current = "eval"
        config = await run.step(current, steps.eval, rubric, trial)
        store.set_status(job_id, S.TRIAL_BUILT)

        current = "candidates"
        registry = await asyncio.to_thread(steps.registry)
        if inspect.isawaitable(registry):
            registry = await registry
        providers = await run.step(current, steps.candidates, spec, registry)

        current = "trial_runner"
        store.set_status(job_id, S.RUNNING)
        try:
            submissions = await run.step(
                current, steps.run_trial, trial, providers, posting.requester_id, on_event
            )
        except RunPaused as paused:
            store.save_submissions(job_id, paused.submissions)
            store.set_status(job_id, S.PAUSED, note=paused.reason)
            run.emit(current, "paused", reason=paused.reason)
            return job_id
        store.save_submissions(job_id, submissions)

        current = "score"
        reports = await run.step(current, steps.score, config, submissions)
        store.save_scores(job_id, reports)
        store.set_status(job_id, S.SCORED)

        current = "rank"
        verdict = await run.step(current, steps.rank, reports, submissions, trial.trial_id)
        store.save_verdict(job_id, verdict)
        store.set_status(job_id, S.RANKED)
        return job_id
    except Exception as exc:
        log.exception("job %s stopped in step %s", job_id, current)
        run.emit(current, "stopped", error=f"{type(exc).__name__}: {exc}")
        raise
