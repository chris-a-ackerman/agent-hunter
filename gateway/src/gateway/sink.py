"""Cost log: one row per model call, plus the month-to-date total the budget guard reads."""

import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field


class LLMCall(BaseModel):
    """One model call as written to the cost log (`llm_calls`)."""

    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    account: str
    component: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    job_id: str | None = None


class CostSink(Protocol):
    def record(self, call: LLMCall) -> None: ...

    def month_to_date(self, account: str) -> float: ...


def _same_month(a: datetime, b: datetime) -> bool:
    a, b = a.astimezone(UTC), b.astimezone(UTC)
    return (a.year, a.month) == (b.year, b.month)


class JsonlCostSink:
    """Append-only JSONL file. Months are calendar months in UTC (Parley refreshes on the 1st)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def record(self, call: LLMCall) -> None:
        line = call.model_dump_json() + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line)

    def month_to_date(self, account: str, *, now: datetime | None = None) -> float:
        now = now or datetime.now(UTC)
        with self._lock:
            if not self.path.exists():
                return 0.0
            lines = self.path.read_text(encoding="utf-8").splitlines()
        total = 0.0
        for line in lines:
            if not line.strip():
                continue
            call = LLMCall.model_validate_json(line)
            if call.account == account and _same_month(call.ts, now):
                total += call.cost_usd
        return total
