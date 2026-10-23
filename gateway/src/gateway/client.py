"""The gateway: one place every model call goes through (plan §4.2, §12, D20)."""

import base64
import copy
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import openai

from gateway.sink import CostSink, LLMCall

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://parley.api.mit.edu/v1"
DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_BUDGET_USD = 25.0
COST_HEADER = "x-parley-v1-cost"


class BudgetExhausted(Exception):
    """Parley returned 402: the account is out of credit until the 1st."""


class BudgetGuardTripped(Exception):
    """Our own guard refused the call: month-to-date spend reached the budget."""


@dataclass(frozen=True)
class GatewayResponse:
    content: str
    parsed: dict[str, Any] | None
    cost_usd: float
    tokens_in: int
    tokens_out: int


def _attach_images(messages: list[dict[str, Any]], images: list[bytes]) -> list[dict[str, Any]]:
    """Copy `messages` and add each PNG as a base64 `image_url` part on the last user message."""
    out = copy.deepcopy(messages)
    for msg in reversed(out):
        if msg.get("role") == "user":
            break
    else:
        raise ValueError("images need a user message to attach to")
    content = msg.get("content")
    parts: list[dict[str, Any]] = (
        [{"type": "text", "text": content}] if isinstance(content, str) else list(content or [])
    )
    for png in images:
        url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        parts.append({"type": "image_url", "image_url": {"url": url}})
    msg["content"] = parts
    return out


def _parse_cost(headers: Any, model: str) -> float:
    raw = headers.get(COST_HEADER)
    if raw is None:
        log.warning("Parley response for %s had no %s header; logging cost 0.0", model, COST_HEADER)
        return 0.0
    try:
        return float(raw)
    except ValueError:
        log.warning("Unparseable %s header %r for %s; logging cost 0.0", COST_HEADER, raw, model)
        return 0.0


def _wants_json(response_format: dict[str, Any] | None) -> bool:
    return response_format is not None and response_format.get("type") in (
        "json_object",
        "json_schema",
    )


class Gateway:
    def __init__(
        self,
        sink: CostSink,
        *,
        account: str | None = None,
        budget_usd: float | None = None,
        client: Any = None,
    ) -> None:
        self.sink = sink
        account = account or os.environ.get("PARLEY_ACCOUNT")
        if not account:
            raise ValueError("Gateway needs an account label (argument or PARLEY_ACCOUNT)")
        self.account: str = account
        if budget_usd is None:
            budget_usd = float(os.environ.get("MONTHLY_BUDGET_USD") or DEFAULT_BUDGET_USD)
        self.budget_usd = budget_usd
        if client is None:
            api_key = os.environ.get("PARLEY_API_KEY")
            if not api_key:
                raise ValueError("PARLEY_API_KEY is not set")
            client = openai.OpenAI(
                base_url=os.environ.get("PARLEY_BASE_URL") or DEFAULT_BASE_URL,
                api_key=api_key,
                max_retries=2,  # 429s: the SDK backs off and honours Retry-After
            )
        self.client = client

    def call(
        self,
        component: str,
        model: str,
        messages: list[dict[str, Any]],
        *,
        response_format: dict[str, Any] | None = None,
        images: list[bytes] | None = None,
        job_id: str | None = None,
        max_tokens: int | None = None,
    ) -> GatewayResponse:
        spent = self.sink.month_to_date(self.account)
        if spent >= self.budget_usd:
            raise BudgetGuardTripped(
                f"account {self.account!r} has spent ${spent:.2f} this month "
                f"(budget ${self.budget_usd:.2f})"
            )

        if images:
            messages = _attach_images(messages, images)
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if response_format is not None:
            kwargs["response_format"] = response_format
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        try:
            raw = self.client.chat.completions.with_raw_response.create(**kwargs)
        except openai.APIStatusError as e:
            if e.status_code == 402:
                raise BudgetExhausted(
                    f"Parley credit exhausted for account {self.account!r}"
                ) from None
            raise

        completion = raw.parse()
        cost = _parse_cost(raw.headers, model)
        usage = completion.usage
        tokens_in = usage.prompt_tokens if usage else 0
        tokens_out = usage.completion_tokens if usage else 0
        content = completion.choices[0].message.content or ""

        self.sink.record(
            LLMCall(
                account=self.account,
                component=component,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                job_id=job_id,
            )
        )

        parsed = None
        if _wants_json(response_format):
            try:
                loaded = json.loads(content)
                parsed = loaded if isinstance(loaded, dict) else None
            except json.JSONDecodeError:
                parsed = None
        return GatewayResponse(content, parsed, cost, tokens_in, tokens_out)
