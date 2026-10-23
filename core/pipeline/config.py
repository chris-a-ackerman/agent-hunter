"""Pipeline configuration read from the environment (D22).

`SCORER=mock|real` picks the Scorer: `mock` (default until integration day) is
`mocks.fake_scorer`, `real` is `core.eval.scorer` (AGE-19).
"""

import os
from typing import Literal, cast

from contracts.steps import Score

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
