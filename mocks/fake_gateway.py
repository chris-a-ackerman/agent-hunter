"""Fake model gateway: replays scripted replies instead of calling Parley, so LLM steps can be
unit-tested offline. Has the same `call(...)` as `gateway.Gateway` and records every request.
"""

import json
from typing import Any

from gateway import GatewayResponse


class FakeGateway:
    def __init__(self, *replies: str | dict[str, Any]) -> None:
        """Each reply is returned by one `call`, in order: a dict is sent as its JSON, a string
        as is (e.g. "not json"). Calling more often than there are replies raises."""
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append(
            {
                "component": component,
                "model": model,
                "messages": messages,
                "response_format": response_format,
                "max_tokens": max_tokens,
            }
        )
        if not self.replies:
            raise AssertionError("FakeGateway: no scripted reply left")
        reply = self.replies.pop(0)
        content = json.dumps(reply) if isinstance(reply, dict) else reply
        parsed = None
        if response_format is not None:
            try:
                loaded = json.loads(content)
                parsed = loaded if isinstance(loaded, dict) else None
            except json.JSONDecodeError:
                parsed = None
        return GatewayResponse(content, parsed, 0.0, 0, 0)
