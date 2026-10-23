"""Eval Builder → Scorer → Ranker (plan §8)."""

from typing import Literal

from pydantic import Field, model_validator

from contracts.base import CI95, Contract, DocId, ProviderId, Score
from contracts.invoice import Invoice
from contracts.rubric import Tolerances, Weights


class ScoringConfig(Contract):
    """Built by the Eval Builder; the pipeline passes it to the Scorer untouched."""

    trial_id: str
    weights: Weights
    tolerances: Tolerances
    answer_keys: dict[DocId, Invoice]
    consistency_checks: list[str]


# Submission statuses, plus `invalid`: the provider answered but the output isn't a valid
# Invoice (wrong types, missing or extra keys). Everything except `ok` scores 0.
DocScoreStatus = Literal["ok", "invalid", "timeout", "error", "not_run"]


class DocScore(Contract):
    doc_id: DocId
    score: Score
    field_scores: dict[str, float]
    status: DocScoreStatus
    sum_check_ok: bool | None = Field(description="Line-items sum check; None if not checked")


class ScoreReport(Contract):
    """One per provider."""

    provider: ProviderId
    doc_scores: list[DocScore]
    trial_score: Score
    ci_95: CI95
    n_docs: int = Field(ge=0)

    @model_validator(mode="after")
    def _n_docs_matches(self) -> "ScoreReport":
        if self.n_docs != len(self.doc_scores):
            raise ValueError("n_docs must equal len(doc_scores)")
        return self
