"""The mocks' corruption model (AGE-18).

For each of the six scored fields independently, with probability 1 - p the value is replaced
by a clearly wrong one, so that field scores 0. The expected weighted doc score is then p,
whatever the rubric weights. The Scorer's accuracy test (AGE-19) calls `mock_output` in-process.
"""

import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from faker import Faker

from contracts import Invoice
from contracts.rubric import SCORED_FIELDS

Behaviour = Literal["corrupt", "timeout", "invalid"]


@dataclass(frozen=True)
class MockSpec:
    slug: str
    behaviour: Behaviour
    p: float | None  # known accuracy; None when the mock never answers
    cost_usd: float | None
    price_per_doc_usd: float


MOCKS: dict[str, MockSpec] = {
    m.slug: m
    for m in [
        MockSpec("mock-a", "corrupt", 0.95, 0.004, 0.010),
        MockSpec("mock-b", "corrupt", 0.80, 0.001, 0.003),
        MockSpec("mock-c", "corrupt", 0.60, 0.0005, 0.002),
        MockSpec("mock-d", "timeout", None, None, 0.005),
        MockSpec("mock-e", "invalid", 0.0, 0.001, 0.004),
    ]
}

# Valid JSON, but not an Invoice: the Trial Runner records `ok`, the Scorer marks it `invalid`.
INVALID_OUTPUT: dict[str, Any] = {"text": "not json"}

_CURRENCIES = ("USD", "EUR", "GBP", "JPY", "CHF", "CAD")
_faker = Faker("en_US")


def _wrong_vendor(name: str, rng: random.Random) -> str:
    _faker.seed_instance(rng.getrandbits(32))
    while (other := _faker.company()).lower() == name.lower():
        pass
    return other


def _wrong_value(field: str, out: dict[str, Any], rng: random.Random) -> Any:
    v = out[field]
    match field:
        case "total":
            return round(v * 1.1, 2) if v else 1.0
        case "vendor_name":
            return _wrong_vendor(v, rng)
        case "invoice_date":
            return (date.fromisoformat(v) + timedelta(days=37)).isoformat()
        case "invoice_number":
            return f"X{rng.getrandbits(32):08X}"
        case "currency":
            return rng.choice([c for c in _CURRENCIES if c != v.upper()])
        case "line_items":
            return [
                {
                    **i,
                    "unit_price": round(i["unit_price"] * 1.5, 2),
                    "amount": round(i["amount"] * 1.5, 2),
                }
                for i in v
            ]
    raise ValueError(f"unknown field {field!r}")


def corrupt(truth: Invoice, p: float, rng: random.Random) -> tuple[dict[str, Any], frozenset[str]]:
    """Returns (output, the fields that were corrupted). Each field is kept with probability p."""
    out = truth.model_dump()
    # Draw every keep/corrupt decision first, so the decisions don't depend on the values.
    hit = frozenset(f for f in SCORED_FIELDS if rng.random() >= p)
    for f in SCORED_FIELDS:
        if f in hit:
            out[f] = _wrong_value(f, out, rng)
    return out, hit


def mock_output(slug: str, doc_id: str, truth: Invoice) -> dict[str, Any]:
    """What mock `slug` answers for this document. Deterministic per (slug, doc_id)."""
    spec = MOCKS[slug]
    if spec.behaviour == "invalid":
        return dict(INVALID_OUTPUT)
    if spec.behaviour != "corrupt" or spec.p is None:
        raise ValueError(f"{slug} doesn't produce output")
    output, _ = corrupt(truth, spec.p, random.Random(f"{slug}:{doc_id}"))
    return output
