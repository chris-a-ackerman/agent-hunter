"""The A2A door's offline mode (AGE-45): a whole hire with no model call and no Parley key. Real
mock-provider and doc servers on free ports, the real Trial Runner and Scorer, the stub agents."""

import json
import socket

import pytest
from fastapi.testclient import TestClient

import core.agents.job_reviewer
import gateway
from contracts import Verdict
from core import a2a
from core.pipeline.stubs import ModelCallForbidden, NoModelGateway, forbid_model_calls
from examples.make_examples import EXAMPLES_DIR
from tests.test_pipeline.conftest import load_example


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def model_calls(monkeypatch):
    """Every way a step could reach a model, counted and refused."""
    calls = []

    def refuse(*args, **kwargs):
        calls.append(args)
        raise AssertionError("offline mode made a model call")

    monkeypatch.setattr(gateway.Gateway, "call", refuse)
    monkeypatch.setattr(core.agents.job_reviewer, "default_gateway", refuse)
    return calls


@pytest.fixture
def offline(monkeypatch, tmp_path, model_calls):
    for key in ("PARLEY_API_KEY", "PARLEY_ACCOUNT", "PARLEY_BASE_URL", "A2A_PUBLIC_URL"):
        monkeypatch.delenv(key, raising=False)
    env = {
        "DOCS_BASE_URL": f"http://127.0.0.1:{free_port()}",
        "MOCK_PROVIDERS_BASE_URL": f"http://127.0.0.1:{free_port()}",
        "DOCS_DIR": str(tmp_path / "docs"),
        "TRUTH_DIR": str(tmp_path / "truth"),
        "STORE_DIR": str(tmp_path / "store"),
        "SAMPLES_DIR": str(EXAMPLES_DIR),
        "MOCK_TASK_DEADLINE_MS": "300",  # mock-d sleeps 2 × deadline: it must time out
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return TestClient(a2a.create_app(mode="offline"))


def hire(c: TestClient, example: str) -> dict:
    body = {
        "job": load_example(example).model_dump(mode="json"),
        "request_id": "order-1",
        "nonce": "1",
    }
    envelope = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "messageId": "m-1",
                "parts": [{"kind": "text", "text": json.dumps(body)}],
            }
        },
    }
    return c.post("/", json=envelope).json()["result"]


def test_offline_hire_needs_no_model_and_says_so(offline, model_calls):
    assert offline.get("/.well-known/agent-card.json").json()["mode"] == "offline"
    task = hire(offline, "invoice_clean")
    assert task["status"]["state"] == "completed", task["status"]
    [artifact] = task["artifacts"]
    result = json.loads(artifact["parts"][0]["text"])
    assert result["mode"] == "offline" and result["job_status"] == "ranked"
    verdict = Verdict.model_validate(result["verdict"])
    rows = {row.provider: row for row in verdict.ranking}
    assert set(rows) == {"mock-a@1", "mock-b@1", "mock-c@1", "mock-d@1", "mock-e@1"}
    # Our provider-failure boundary case: the always-slow mock is ranked, with its timeouts.
    assert rows["mock-d@1"].timeouts > 0 and rows["mock-d@1"].accuracy == 0
    assert rows["mock-a@1"].accuracy > rows["mock-c@1"].accuracy  # the real Scorer at work
    assert result["spend_usd"]["platform_usd"] == 0
    assert result["spend_usd"]["providers_usd"] > 0  # the mocks report a fixed cost_usd
    assert model_calls == []


def test_offline_unscorable_job_is_rejected_without_provider_spend(offline, model_calls):
    result = json.loads(hire(offline, "reject_unscorable")["artifacts"][0]["parts"][0]["text"])
    assert result["mode"] == "offline" and result["job_status"] == "rejected"
    assert result["spend_usd"] == {"platform_usd": 0, "providers_usd": 0, "total_usd": 0}
    assert model_calls == []


def test_offline_uses_its_own_store_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("STORE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    app = a2a.create_app(mode="offline", serve_docs=False)
    assert app.title == "Agent-Hunter A2A"
    assert not (tmp_path / ".data" / "store").exists()


@pytest.mark.parametrize(
    "value, expected", [("1", True), ("true", True), ("", False), ("0", False)]
)
def test_offline_can_be_switched_on_by_env(monkeypatch, value, expected):
    monkeypatch.setenv("AGENT_HUNTER_OFFLINE", value)
    assert a2a.offline_requested() is expected


def test_forbid_model_calls_makes_every_call_raise(monkeypatch):
    # Saved first, so monkeypatch puts the real ones back after the test.
    monkeypatch.setattr(gateway.Gateway, "call", gateway.Gateway.call)
    monkeypatch.setattr(
        core.agents.job_reviewer, "default_gateway", core.agents.job_reviewer.default_gateway
    )
    forbid_model_calls()
    with pytest.raises(ModelCallForbidden):
        core.agents.job_reviewer.default_gateway()
    with pytest.raises(ModelCallForbidden):
        gateway.Gateway.call(object(), "component", "model", [])
    with pytest.raises(ModelCallForbidden):
        NoModelGateway().call("component", "model", [])
