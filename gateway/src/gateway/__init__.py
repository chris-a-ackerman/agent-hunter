"""Model gateway: every LLM call goes through MIT Parley with a cost log and budget guard (D20)."""

from gateway.client import (
    DEFAULT_MODEL,
    FREE_MODEL,
    BudgetExhausted,
    BudgetGuardTripped,
    Gateway,
    GatewayResponse,
)
from gateway.sink import CostSink, JsonlCostSink, LLMCall

__all__ = [
    "DEFAULT_MODEL",
    "FREE_MODEL",
    "BudgetExhausted",
    "BudgetGuardTripped",
    "CostSink",
    "Gateway",
    "GatewayResponse",
    "JsonlCostSink",
    "LLMCall",
]
