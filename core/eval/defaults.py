"""Default scoring weights (plan §8). The Rubric Builder (AGE-21) starts from these."""

from contracts.rubric import SCORED_FIELDS

DEFAULT_WEIGHTS: dict[str, float] = {
    "total": 0.30,
    "line_items": 0.30,
    "vendor_name": 0.15,
    "invoice_date": 0.15,
    "invoice_number": 0.05,
    "currency": 0.05,
}

assert set(DEFAULT_WEIGHTS) == set(SCORED_FIELDS)
