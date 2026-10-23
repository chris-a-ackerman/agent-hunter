"""Bootstrap confidence intervals and the recommend / tie / insufficient decision (plan §8).

Pure functions, numpy only. The Scorer (AGE-19) calls `bootstrap_ci` with `seed_for(trial_id)`,
so a rerun of the same trial gives identical CIs; the Ranker (AGE-24) builds `RankingRow`s and
calls `decide`.
"""

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from contracts import RankingRow


def seed_for(trial_id: str) -> int:
    """A stable seed from a trial ID (unlike hash(), the same in every process)."""
    return int.from_bytes(hashlib.sha256(trial_id.encode()).digest()[:8], "big")


def bootstrap_ci(
    doc_scores: Sequence[float],
    *,
    n_resamples: int = 1000,
    seed: int = 0,
    level: float = 0.95,
) -> tuple[float, float]:
    """Percentile-bootstrap CI on the mean doc score. One document → (s, s)."""
    if not doc_scores:
        raise ValueError("bootstrap_ci needs at least one document score")
    if n_resamples < 1 or not 0 < level < 1:
        raise ValueError("need n_resamples >= 1 and 0 < level < 1")
    scores = np.asarray(doc_scores, dtype=float)
    if len(scores) == 1:
        return float(scores[0]), float(scores[0])

    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(scores), size=(n_resamples, len(scores)))
    means = scores[idx].mean(axis=1)
    alpha = (1 - level) / 2
    low, high = np.quantile(means, [alpha, 1 - alpha])
    # Clamp float noise so the result always satisfies the CI95 contract (0 <= low <= high <= 1).
    low, high = float(np.clip(low, 0, 1)), float(np.clip(high, 0, 1))
    return low, max(low, high)


@dataclass
class Decision:
    status: Literal["recommended", "tie", "insufficient_candidates"]
    winner: str | None  # provider ID ("slug@version")
    tied: list[str] = field(default_factory=list)


def is_eligible(row: RankingRow) -> bool:
    """At least one `ok` submission. The Ranker sets mean_latency_ms as the mean over `ok`
    submissions, so it is None exactly when a provider never answered."""
    return row.mean_latency_ms is not None


def _tie_break_key(row: RankingRow) -> tuple[float, float, str]:
    cost = math.inf if row.cost_per_doc is None else row.cost_per_doc
    latency = math.inf if row.mean_latency_ms is None else row.mean_latency_ms
    return cost, latency, row.provider


def decide(rows: list[RankingRow]) -> Decision:
    """Recommend the leader only if no eligible provider's CI overlaps its CI; otherwise a tie
    broken by lowest cost per doc, then lowest latency, then provider ID. Fewer than two
    eligible providers → insufficient_candidates."""
    eligible = [r for r in rows if is_eligible(r)]
    if len(eligible) < 2:
        return Decision("insufficient_candidates", None)

    # Equal accuracy is ordered by the tie-break, so the leader is deterministic.
    ranked = sorted(eligible, key=lambda r: (-r.accuracy, *_tie_break_key(r)))
    leader = ranked[0]
    tied = [r for r in ranked if r is leader or r.ci_95[1] >= leader.ci_95[0]]
    if len(tied) == 1:
        return Decision("recommended", leader.provider)
    winner = min(tied, key=_tie_break_key)
    return Decision("tie", winner.provider, [r.provider for r in tied])
