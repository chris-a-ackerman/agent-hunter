"""The invoice output schema (plan §1.3). This is what providers must return.

Validation is types-only on purpose: date and currency formats are hints in the JSON Schema,
not enforced here. A badly formatted field costs that field at scoring time (the Scorer
normalizes, plan §8); only a wrong type or a missing/extra key makes the whole output invalid.
"""

from typing import Any

from pydantic import Field

from contracts.base import Contract


class LineItem(Contract):
    description: str
    quantity: float
    unit_price: float
    amount: float


class Invoice(Contract):
    vendor_name: str
    invoice_number: str | None
    invoice_date: str = Field(description="ISO 8601 date, YYYY-MM-DD")
    currency: str = Field(description="ISO 4217 code, e.g. USD")
    line_items: list[LineItem]
    subtotal: float | None
    tax: float | None
    total: float


# Sent to providers as ProviderTask.output_schema. Every field is required (nullable ones
# included) and additionalProperties is false, so it also works with strict JSON-schema modes.
INVOICE_JSON_SCHEMA: dict[str, Any] = Invoice.model_json_schema()
