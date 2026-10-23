"""The Rubric Builder's output (plan §8)."""

from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, Field

from contracts.base import Contract

# The six scored invoice fields of plan §8. Weights cover exactly these keys.
SCORED_FIELDS: tuple[str, ...] = (
    "total",
    "line_items",
    "vendor_name",
    "invoice_date",
    "invoice_number",
    "currency",
)


def _check_weights(weights: dict[str, float]) -> dict[str, float]:
    if set(weights) != set(SCORED_FIELDS):
        raise ValueError(f"weights must have exactly the keys {sorted(SCORED_FIELDS)}")
    if any(w < 0 for w in weights.values()):
        raise ValueError("weights must be non-negative")
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"weights must sum to 1 (±1e-6), got {total}")
    return weights


Weights = Annotated[dict[str, float], AfterValidator(_check_weights)]


class Tolerances(Contract):
    total_abs: float = Field(default=0.01, ge=0)
    amount_abs: float = Field(default=0.01, ge=0)
    vendor_fuzzy_min: float = Field(default=0.90, ge=0, le=1)
    line_item_desc_min: float = Field(default=0.80, ge=0, le=1)


class Rubric(Contract):
    rubric_id: str
    job_id: str
    weights: Weights
    tolerances: Tolerances = Field(default_factory=Tolerances)
    required_fields: list[str]
    consistency_checks: list[str] = Field(default_factory=lambda: ["line_items_sum"])
    approved_by: str = "auto"
    approved_at: datetime
