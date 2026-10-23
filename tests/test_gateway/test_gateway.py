import base64
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx2
import openai
import pytest
from openai.types.chat import ChatCompletion

from gateway import (
    DEFAULT_MODEL,
    BudgetExhausted,
    BudgetGuardTripped,
    Gateway,
    JsonlCostSink,
    LLMCall,
)

PNG = b"\x89PNG\r\n\x1a\nfake"


class FakeRaw:
    def __init__(self, completion: ChatCompletion, headers: dict[str, str]) -> None:
        self._completion = completion
        self.headers = headers

    def parse(self) -> ChatCompletion:
        return self._completion


class FakeClient:
    """Mimics `client.chat.completions.with_raw_response.create(...)` and records each request."""

    def __init__(
        self,
        content: str = "ok",
        headers: dict[str, str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.content = content
        self.headers = {"x-parley-v1-cost": "0.0042"} if headers is None else headers
        self.error = error
        self.requests: list[dict[str, Any]] = []
        self.chat = self
        self.completions = self
        self.with_raw_response = self

    def create(self, **kwargs: Any) -> FakeRaw:
        self.requests.append(kwargs)
        if self.error:
            raise self.error
        completion = ChatCompletion.model_validate(
            {
                "id": "c1",
                "object": "chat.completion",
                "created": 0,
                "model": kwargs["model"],
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": self.content},
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            }
        )
        return FakeRaw(completion, self.headers)


def status_error(code: int) -> openai.APIStatusError:
    request = httpx2.Request("POST", "https://parley.test/v1/chat/completions")
    return openai.APIStatusError("boom", response=httpx2.Response(code, request=request), body=None)


@pytest.fixture
def sink(tmp_path):
    return JsonlCostSink(tmp_path / "llm_calls.jsonl")


def make_gateway(sink, client, budget_usd=25.0):
    return Gateway(sink, account="test", budget_usd=budget_usd, client=client)


USER = [{"role": "user", "content": "hi"}]


def test_call_logs_cost_row(sink):
    resp = make_gateway(sink, FakeClient()).call("job_reviewer", DEFAULT_MODEL, USER, job_id="j1")

    assert (resp.content, resp.cost_usd, resp.tokens_in, resp.tokens_out) == ("ok", 0.0042, 12, 3)
    rows = [LLMCall.model_validate_json(line) for line in sink.path.read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert (row.account, row.component, row.model, row.job_id) == (
        "test",
        "job_reviewer",
        DEFAULT_MODEL,
        "j1",
    )
    assert (row.tokens_in, row.tokens_out, row.cost_usd) == (12, 3, 0.0042)


def test_missing_cost_header_logs_zero_and_warns(sink, caplog):
    with caplog.at_level(logging.WARNING, logger="gateway.client"):
        resp = make_gateway(sink, FakeClient(headers={})).call("c", DEFAULT_MODEL, USER)
    assert resp.cost_usd == 0.0
    assert "x-parley-v1-cost" in caplog.text


def test_guard_trips_at_budget(sink):
    sink.record(LLMCall(account="test", component="c", model="m", cost_usd=0.75))
    client = FakeClient()
    gw = make_gateway(sink, client, budget_usd=1.0)

    gw.call("c", DEFAULT_MODEL, USER)  # 0.75 < 1.0: allowed, adds 0.0042
    sink.record(LLMCall(account="test", component="c", model="m", cost_usd=0.25))
    with pytest.raises(BudgetGuardTripped):
        gw.call("c", DEFAULT_MODEL, USER)
    assert len(client.requests) == 1  # the refused call never reached Parley


def test_guard_ignores_other_accounts_and_past_months(sink):
    sink.record(LLMCall(account="other", component="c", model="m", cost_usd=100))
    sink.record(
        LLMCall(
            account="test",
            component="c",
            model="m",
            cost_usd=100,
            ts=datetime(2020, 1, 15, tzinfo=UTC),
        )
    )
    assert sink.month_to_date("test") == 0.0
    make_gateway(sink, FakeClient(), budget_usd=1.0).call("c", DEFAULT_MODEL, USER)


def test_402_raises_budget_exhausted(sink):
    gw = make_gateway(sink, FakeClient(error=status_error(402)))
    with pytest.raises(BudgetExhausted):
        gw.call("c", DEFAULT_MODEL, USER)
    assert not sink.path.exists()


def test_other_status_errors_propagate(sink):
    gw = make_gateway(sink, FakeClient(error=status_error(500)))
    with pytest.raises(openai.APIStatusError) as info:
        gw.call("c", DEFAULT_MODEL, USER)
    assert not isinstance(info.value, BudgetExhausted)


def test_images_become_data_urls_on_last_user_message(sink):
    client = FakeClient()
    messages = [
        {"role": "system", "content": "extract"},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "page attached"},
    ]
    make_gateway(sink, client).call("c", DEFAULT_MODEL, messages, images=[PNG, PNG])

    sent = client.requests[0]["messages"]
    assert sent[1] == {"role": "user", "content": "first"}
    parts = sent[3]["content"]
    assert parts[0] == {"type": "text", "text": "page attached"}
    url = "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert parts[1:] == [{"type": "image_url", "image_url": {"url": url}}] * 2
    assert messages[3]["content"] == "page attached"  # caller's messages untouched


def test_images_without_user_message_rejected(sink):
    with pytest.raises(ValueError):
        make_gateway(sink, FakeClient()).call(
            "c", DEFAULT_MODEL, [{"role": "system", "content": "x"}], images=[PNG]
        )


def test_json_response_format_parsed(sink):
    client = FakeClient(content=json.dumps({"total": 12.5}))
    resp = make_gateway(sink, client).call(
        "c", DEFAULT_MODEL, USER, response_format={"type": "json_object"}, max_tokens=50
    )
    assert resp.parsed == {"total": 12.5}
    assert client.requests[0]["response_format"] == {"type": "json_object"}
    assert client.requests[0]["max_tokens"] == 50


def test_invalid_json_parsed_is_none(sink):
    resp = make_gateway(sink, FakeClient(content="not json")).call(
        "c", DEFAULT_MODEL, USER, response_format={"type": "json_object"}
    )
    assert resp.parsed is None


def test_no_response_format_means_no_parse(sink):
    client = FakeClient(content="{}")
    resp = make_gateway(sink, client).call("c", DEFAULT_MODEL, USER)
    assert resp.parsed is None
    assert "response_format" not in client.requests[0]


def test_config_from_env(monkeypatch, sink):
    monkeypatch.setenv("PARLEY_ACCOUNT", "envacct")
    monkeypatch.setenv("MONTHLY_BUDGET_USD", "7")
    gw = Gateway(sink, client=FakeClient())
    assert (gw.account, gw.budget_usd) == ("envacct", 7.0)


def test_missing_account_rejected(monkeypatch, sink):
    monkeypatch.delenv("PARLEY_ACCOUNT", raising=False)
    with pytest.raises(ValueError):
        Gateway(sink, client=FakeClient())


def test_missing_key_rejected(monkeypatch, sink):
    monkeypatch.delenv("PARLEY_API_KEY", raising=False)
    with pytest.raises(ValueError, match="PARLEY_API_KEY"):
        Gateway(sink, account="test")


@pytest.mark.live
def test_live_call_logs_cost_row(sink, caplog):
    with caplog.at_level(logging.WARNING, logger="gateway.client"):
        resp = Gateway(sink, account="live-test").call(
            "live_test",
            DEFAULT_MODEL,
            [{"role": "user", "content": "Reply with: ok"}],
            max_tokens=16,
        )
    assert "x-parley-v1-cost" not in caplog.text  # the cost came from the header
    rows = sink.path.read_text().splitlines()
    assert len(rows) == 1
    row = LLMCall.model_validate_json(rows[0])
    assert row.cost_usd == resp.cost_usd
    assert row.tokens_in > 0
