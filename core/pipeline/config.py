"""Pipeline configuration read from the environment (D22).

`build_steps()` wires the eight steps (plus the provider registry) for `run_pipeline`.

`SCORER=mock|real` picks the Scorer: `mock` is
`mocks.fake_scorer`, `real` is `core.eval.scorer` (AGE-19).
"""

import functools
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from contracts.steps import Score

if TYPE_CHECKING:
    from core.pipeline.runner import Steps
    from core.store import Store
    from gateway import Gateway

ScorerMode = Literal["mock", "real"]
Transport = Literal["http", "a2a"]


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


def provider_a2a_urls() -> list[str]:
    """`PROVIDER_A2A_URLS`: comma-separated A2A base URLs, each agent listed by its card."""
    return [u.strip() for u in os.environ.get("PROVIDER_A2A_URLS", "").split(",") if u.strip()]


def provider_transport() -> Transport:
    """`PROVIDER_TRANSPORT=http|a2a` (default http): how tasks reach the `PROVIDER_SLUGS`."""
    mode = os.environ.get("PROVIDER_TRANSPORT", "http").strip().lower() or "http"
    if mode not in ("http", "a2a"):
        raise ValueError(f"PROVIDER_TRANSPORT must be 'http' or 'a2a', got {mode!r}")
    return cast(Transport, mode)


class _DefaultGateway:
    """`core.agents.job_reviewer.default_gateway()`, created on the first model call, so building
    the steps (e.g. importing `core.api`) needs no Parley settings."""

    def call(self, *args: Any, **kwargs: Any) -> Any:
        from core.agents.job_reviewer import default_gateway

        return default_gateway().call(*args, **kwargs)


def build_steps(store: "Store | None" = None, gateway: "Gateway | None" = None) -> "Steps":
    """The pipeline's steps from the environment: all eight are the real ones (the Scorer can
    be `SCORER=mock`). `gateway` is shared by the steps that call a model (Job Reviewer, Rubric
    Builder, Trial Builder, Ranker); default: the job store's cost-logging gateway.

    PROVIDERS_BASE_URL + PROVIDER_SLUGS   provider registry (D22); cards fetched once per run
    PROVIDER_TRANSPORT                    http (default) or a2a: how tasks reach those slugs
    PROVIDER_A2A_URLS                     extra agents listed by A2A base URL (always over A2A)
    DOCS_BASE_URL                         where providers fetch `/d/{doc_id}.pdf` (D17)
    DOCS_DIR, TRUTH_DIR                   trial PDFs and their answer keys (D17)
    SAMPLES_DIR                           base for posting sample paths (default `examples`)
    TASK_DEADLINE_MS, MAX_CONCURRENCY     Trial Runner deadline and cap (core.runner.trial_runner)
    TRIAL_SIZE                            documents per trial (core.agents.trial_builder)
    """
    from core.agents.job_reviewer import review_job
    from core.agents.ranker import rank
    from core.agents.rubric_builder import build_rubric
    from core.agents.trial_builder import build_trial
    from core.eval.eval_builder import build_eval
    from core.pipeline.runner import Steps
    from core.runner.candidate_finder import find_candidates
    from core.runner.registry import refresh_registry
    from core.runner.trial_runner import TrialRunner

    gw = cast("Gateway", gateway or _DefaultGateway())
    samples_dir = Path(_env("SAMPLES_DIR", "examples"))
    return Steps(
        review=functools.partial(review_job, gateway=gw, samples_dir=samples_dir),
        rubric=functools.partial(build_rubric, gateway=gw),
        trial=functools.partial(
            build_trial,
            gateway=gw,
            samples_dir=samples_dir,
            docs_dir=Path(_env("DOCS_DIR", "data/docs")),
            truth_dir=Path(_env("TRUTH_DIR", "data/truth")),
        ),
        eval=build_eval,
        candidates=find_candidates,
        run_trial=TrialRunner.from_env(),
        score=get_scorer(),
        rank=functools.partial(rank, gateway=gw),
        registry=functools.partial(refresh_registry, store),
    )
