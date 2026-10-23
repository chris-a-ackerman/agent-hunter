"""Pipeline configuration read from the environment (D22).

`build_steps()` wires the eight steps (plus the provider registry) for `run_pipeline`.

`SCORER=mock|real` picks the Scorer: `mock` (default until integration day) is
`mocks.fake_scorer`, `real` is `core.eval.scorer` (AGE-19).
"""

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


def build_steps(store: "Store | None" = None) -> "Steps":
    """The pipeline's steps from the environment. Steps that don't exist yet are the stubs in
    `core.pipeline.stubs`; the Eval Builder and the Scorer are real (or `SCORER=mock`).

    PROVIDERS_BASE_URL + PROVIDER_SLUGS   provider registry (D22); cards fetched once per run
    DOCS_BASE_URL                         where providers fetch `/d/{doc_id}.pdf` (D17)
    DOCS_DIR, TRUTH_DIR                   trial PDFs and their answer keys (D17)
    SAMPLES_DIR                           base for posting sample paths (default `examples`)
    TRIAL_DEADLINE_MS                     per-task deadline sent to providers (default 60000)
    """
    from core.eval.eval_builder import build_eval
    from core.pipeline import stubs
    from core.pipeline.registry import load_registry
    from core.pipeline.runner import Steps

    base_url = _env("PROVIDERS_BASE_URL", "http://localhost:8100")
    slugs = provider_slugs()
    return Steps(
        review=stubs.review_job_stub,
        rubric=stubs.build_rubric_stub,
        trial=stubs.SampleTrialBuilder(
            samples_dir=Path(_env("SAMPLES_DIR", "examples")),
            docs_dir=Path(_env("DOCS_DIR", "data/docs")),
            truth_dir=Path(_env("TRUTH_DIR", "data/truth")),
        ),
        eval=build_eval,
        candidates=stubs.find_candidates_stub,
        run_trial=stubs.HttpTrialRunner(
            _env("DOCS_BASE_URL", "http://localhost:8200"),
            deadline_ms=int(_env("TRIAL_DEADLINE_MS", "60000")),
        ),
        score=get_scorer(),
        rank=stubs.rank_stub,
        registry=lambda: load_registry(base_url, slugs, store=store),
    )
