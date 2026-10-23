"""The Ranker's output (plan §5.1, §8)."""

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from contracts.base import CI95, Contract, ProviderId, Score, UsdAmount


class RankingRow(Contract):
    provider: ProviderId
    accuracy: Score
    ci_95: CI95
    cost_per_doc: UsdAmount | None = Field(
        description="Mean cost_usd over this provider's submitted tasks (D19)"
    )
    timeouts: int = Field(ge=0)
    mean_latency_ms: float | None = Field(ge=0)


class Verdict(Contract):
    """status semantics:

    - recommended: the leader's CI doesn't overlap the runner-up's. `recommended` = leader.
    - tie: CIs overlap. `tied` = the overlapping group (≥ 2); `recommended` = its tie-break
      winner (lowest cost per doc, then lowest latency). `tie` is true.
    - insufficient_candidates: fewer than 2 providers answered. `recommended` is None.
    """

    status: Literal["recommended", "tie", "insufficient_candidates"]
    recommended: ProviderId | None
    tied: list[ProviderId] = Field(default_factory=list)
    trial_accuracy: Score | None = Field(description="The recommended provider's trial score")
    ci_95: CI95 | None
    cost_per_doc: UsdAmount | None
    valid_until: date | None
    tie: bool
    ranking: list[RankingRow]
    rationale: str
    evidence: str = Field(description="The trial_id the verdict rests on")

    @model_validator(mode="after")
    def _consistent(self) -> "Verdict":
        if self.tie != (self.status == "tie"):
            raise ValueError("tie must be true exactly when status is 'tie'")
        if self.status == "insufficient_candidates":
            if self.recommended is not None:
                raise ValueError("insufficient_candidates must not recommend a provider")
        elif self.recommended is None:
            raise ValueError(f"status '{self.status}' needs a recommended provider")
        if self.status == "tie":
            if len(self.tied) < 2 or self.recommended not in self.tied:
                raise ValueError("a tie needs ≥ 2 tied providers including the recommended one")
        elif self.tied:
            raise ValueError("tied must be empty unless status is 'tie'")
        return self
