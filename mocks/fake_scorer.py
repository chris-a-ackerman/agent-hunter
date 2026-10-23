"""Fake Scorer (AGE-16): random but schema-valid ScoreReports, so the pipeline can be wired and
tested before the real Scorer (AGE-19) is plugged in (plan §11.2 rule 2).

`score` satisfies `contracts.steps.Score`, like `core.eval.scorer.score`. The scores mean
nothing: each provider gets a seeded base accuracy in [0.5, 0.98], and each document scores
base ± noise. Same inputs → identical output. Deliberately imports nothing from `core/eval/`.
"""

import hashlib
import random

from pydantic import ValidationError

from contracts import DocScore, Invoice, ScoreReport, ScoringConfig, Submission
from contracts.scoring import DocScoreStatus

BASE_RANGE = (0.5, 0.98)
NOISE = 0.1
N_RESAMPLES = 200


def _seed(trial_id: str, provider: str) -> int:
    """Stable across processes, unlike hash()."""
    digest = hashlib.sha256(f"{trial_id}:{provider}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _bootstrap_ci(scores: list[float], seed: int) -> tuple[float, float]:
    """Percentile bootstrap of the mean, 95%."""
    rng = random.Random(seed)
    n = len(scores)
    means = sorted(sum(rng.choices(scores, k=n)) / n for _ in range(N_RESAMPLES))
    low = means[int(0.025 * (N_RESAMPLES - 1))]
    high = means[int(0.975 * (N_RESAMPLES - 1))]
    return min(1.0, max(0.0, low)), min(1.0, max(0.0, high))


def _doc_score(config: ScoringConfig, sub: Submission, base: float, rng: random.Random) -> DocScore:
    noise = rng.uniform(-NOISE, NOISE)  # drawn for every doc, so later docs don't shift
    status: DocScoreStatus = sub.status
    if status == "ok":
        try:
            Invoice.model_validate(sub.output)
        except ValidationError:
            status = "invalid"
    value = min(1.0, max(0.0, base + noise)) if status == "ok" else 0.0
    return DocScore(
        doc_id=sub.doc_id,
        score=value,
        field_scores=dict.fromkeys(config.weights, value),
        status=status,
        sum_check_ok=None,
    )


def score(config: ScoringConfig, submissions: list[Submission]) -> list[ScoreReport]:
    """One ScoreReport per provider, in the order providers first appear in `submissions`."""
    unknown = {s.doc_id for s in submissions} - set(config.answer_keys)
    if unknown:
        raise ValueError(f"submissions for documents not in the answer key: {sorted(unknown)}")

    by_provider: dict[str, list[Submission]] = {}
    for sub in submissions:
        by_provider.setdefault(sub.provider, []).append(sub)

    reports = []
    for provider, subs in by_provider.items():
        seed = _seed(config.trial_id, provider)
        rng = random.Random(seed)
        base = rng.uniform(*BASE_RANGE)
        docs = [_doc_score(config, sub, base, rng) for sub in subs]
        scores = [d.score for d in docs]
        reports.append(
            ScoreReport(
                provider=provider,
                doc_scores=docs,
                trial_score=min(1.0, sum(scores) / len(scores)),
                ci_95=_bootstrap_ci(scores, seed),
                n_docs=len(docs),
            )
        )
    return reports
