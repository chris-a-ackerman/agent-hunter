"""Shared base classes and field types for every contract."""

import uuid
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints


class Contract(BaseModel):
    """Internal contract: unknown fields are rejected so drift between workstreams fails loudly."""

    model_config = ConfigDict(extra="forbid")


class ExternalContract(BaseModel):
    """Contract parsed from a provider's response: unknown fields are ignored, not fatal."""

    model_config = ConfigDict(extra="ignore")


# D17: documents are named by an opaque ID that never encodes layout, pool or "trial".
DocId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{12}$")]

# "{slug}@{version}", e.g. "acme@1.2.0". Used as the provider ID everywhere.
ProviderId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]*@[^@\s]+$")]

Score = Annotated[float, Field(ge=0.0, le=1.0)]
UsdAmount = Annotated[float, Field(ge=0.0)]


def _check_ci(ci: tuple[float, float]) -> tuple[float, float]:
    low, high = ci
    if not 0.0 <= low <= high <= 1.0:
        raise ValueError(f"ci_95 must satisfy 0 <= low <= high <= 1, got {ci}")
    return ci


# 95% confidence interval (low, high) on a 0..1 score.
CI95 = Annotated[tuple[float, float], AfterValidator(_check_ci)]


def new_doc_id() -> str:
    """A fresh opaque document ID (D17)."""
    return uuid.uuid4().hex[:12]
