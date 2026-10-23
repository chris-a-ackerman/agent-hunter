"""Pipeline configuration read from the environment (D22).

`build_steps()` wires the eight steps (plus the provider registry) for `run_pipeline`.

`SCORER=mock|real` picks the Scorer: `mock` (default until integration day) is
`mocks.fake_scorer`, `real` is `core.eval.scorer` (AGE-19).
"""

import functools
import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from contracts.steps import Score

if TYPE_CHECKING:
    from core.pipeline.runner import Steps
    from core.store import Store

ScorerMode = Literal["mock", "real"]


def scorer_mode() -> ScorerMode:
    mode = os.environ.get("SCORER", "mock").strip().lower() or "mock"
    if mode not in ("mock", "real"):
        raise ValueError(f"SCORER must be 'mock' or 'real', got {mode!r}")
    return cast(ScorerMode, mode)


def get_scorer() -> Score:
    """The Scorer selected by SCORER; imported lazily so `mock` needs no eval dependencies."""
    if scorer_mode() == "real":
        from core.eval.scorer import score
    else:
        from mocks.fake_scorer import score
    return score


def _env(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


def provider_slugs() -> list[str]:
    return [s.strip() for s in os.environ.get("PROVIDER_SLUGS", "").split(",") if s.strip()]


def providers_base_url() -> str:
    return _env("PROVIDERS_BASE_URL", "http://localhost:8100")


def build_steps(store: "Store | None" = None) -> "Steps":
    """The pipeline's steps from the environment. Steps that don't exist yet are the stubs in
    `core.pipeline.stubs`; the Job Reviewer, the Eval Builder, the Candidate Finder, the Trial
    Runner and the Scorer are real (or `SCORER=mock`).

    PROVIDERS_BASE_URL + PROVIDER_SLUGS   provider registry (D22); cards fetched once per run
    DOCS_BASE_URL                         where providers fetch `/d/{doc_id}.pdf` (D17)
    DOCS_DIR, TRUTH_DIR                   trial PDFs and their answer keys (D17)
    SAMPLES_DIR                           base for posting sample paths (default `examples`)
    TASK_DEADLINE_MS, MAX_CONCURRENCY     Trial Runner deadline and cap (core.runner.trial_runner)
    """
    from core.agents.job_reviewer import review_job
    from core.eval.eval_builder import build_eval
    from core.pipeline import stubs
    from core.pipeline.runner import Steps
    from core.runner.candidate_finder import find_candidates
    from core.runner.registry import refresh_registry
    from core.runner.trial_runner import TrialRunner

    samples_dir = Path(_env("SAMPLES_DIR", "examples"))
    return Steps(
        review=functools.partial(review_job, samples_dir=samples_dir),
        rubric=stubs.build_rubric_stub,
        trial=stubs.SampleTrialBuilder(
            samples_dir=samples_dir,
            docs_dir=Path(_env("DOCS_DIR", "data/docs")),
            truth_dir=Path(_env("TRUTH_DIR", "data/truth")),
        ),
        eval=build_eval,
        candidates=find_candidates,
        run_trial=TrialRunner.from_env(),
        score=get_scorer(),
        rank=stubs.rank_stub,
        registry=functools.partial(refresh_registry, store),
    )
