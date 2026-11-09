"""The A2A front door (AGE-44): the card, and message/send running the whole pipeline. Stub steps
and in-process mock providers (no model calls); `make_steps` and `store` come from
tests/test_pipeline/conftest.py."""

import json
import re

import pytest
from fastapi.testclient import TestClient

from contracts import Verdict
from core.a2a import VERSION, agent_card, create_app
from tests.test_pipeline.conftest import load_example, make_steps, store  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("SAMPLES_DIR", "unused")  # the stub Trial Builder reads examples/ directly
    monkeypatch.delenv("A2A_PUBLIC_URL", raising=False)


def client(store, steps, **kw) -> TestClient:  # noqa: F811
    return TestClient(create_app(store, build_steps=lambda *_: steps, serve_docs=False, **kw))


def send(c: TestClient, body, rpc_id=1) -> dict:
    text = body if isinstance(body, str) else json.dumps(body)
    envelope = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "messageId": "m-1",
                "parts": [{"kind": "text", "text": text}],
            }
        },
    }
    r = c.post("/", json=envelope)
    assert r.status_code == 200
    return r.json()


def hire_request(example: str = "invoice_clean", request_id: str = "order-abc") -> dict:
    job = load_example(example).model_dump(mode="json")
    return {"job": job, "request_id": request_id, "nonce": "abc"}


def result_text(task: dict) -> str:
    texts = [p["text"] for a in task["artifacts"] for p in a["parts"] if p["kind"] == "text"]
    assert len(texts) == 1  # Town's strict evaluator wants exactly one text part
    return texts[0]


def error_of(task: dict) -> dict:
    [part] = task["status"]["message"]["parts"]
    return json.loads(part["text"])


def test_card_is_a_stable_a2a_card(store, make_steps):  # noqa: F811
    c = client(store, make_steps())
    first = c.get("/.well-known/agent-card.json")
    assert first.content == c.get("/.well-known/agent-card.json").content  # Town pins its digest
    card = first.json()
    for key in ("name", "version", "url", "skills", "defaultInputModes"):  # the probe's keys
        assert card[key]
    assert card["name"] == "agent-hunter" and card["url"] == "http://127.0.0.1:8940/"
    assert card["skills"][0]["id"] == "trial_then_hire"
    assert card["mode"] == "live"
    assert re.fullmatch(r"0\.1\.0\+([0-9a-f]{4,}|unknown)", card["version"])
    assert card["version"] == VERSION


def test_card_url_follows_the_public_url(monkeypatch):
    monkeypatch.setenv("A2A_PUBLIC_URL", "https://hunter.example")
    app = create_app(serve_docs=False, tasks_dir=None)
    assert (
        TestClient(app).get("/.well-known/agent-card.json").json()["url"]
        == "https://hunter.example/"
    )
    assert agent_card("http://x:1/", "offline")["mode"] == "offline"


def test_message_send_runs_the_pipeline_and_returns_one_hire_result(store, make_steps):  # noqa: F811
    c = client(store, make_steps())
    task = send(c, hire_request())["result"]
    assert task["kind"] == "task" and task["status"]["state"] == "completed"
    text = result_text(task)
    result = json.loads(text)
    assert text == json.dumps(result, sort_keys=True, separators=(",", ":"))
    assert result["request_id"] == "order-abc" and result["mode"] == "live"
    assert result["job_status"] == "ranked" and result["reason"] is None
    verdict = Verdict.model_validate(result["verdict"])
    assert len(verdict.ranking) == 5
    record = store.get_job(result["job_id"])
    spend = result["spend_usd"]
    assert spend["providers_usd"] == pytest.approx(sum(s.cost_usd or 0 for s in record.submissions))
    assert spend["providers_usd"] > 0
    assert spend["total_usd"] == pytest.approx(spend["platform_usd"] + spend["providers_usd"])


def test_the_answer_key_never_leaves_the_store(store, make_steps):  # noqa: F811
    c = client(store, make_steps())
    task = send(c, hire_request())["result"]
    record = store.get_job(json.loads(result_text(task))["job_id"])
    reply = json.dumps(task) + c.get("/.well-known/agent-card.json").text
    for doc in record.trial.documents:
        assert doc.answer.vendor_name not in reply
        assert doc.answer.invoice_number is None or doc.answer.invoice_number not in reply


def test_rejected_posting_is_completed_without_provider_spend(store, make_steps):  # noqa: F811
    c = client(store, make_steps())
    result = json.loads(result_text(send(c, hire_request("reject_unscorable"))["result"]))
    assert result["job_status"] == "rejected" and result["verdict"] is None
    assert result["reason"].startswith("Not an invoice-extraction job")
    assert result["spend_usd"]["providers_usd"] == 0


def test_paused_job_is_completed_with_the_reason(store, make_steps):  # noqa: F811
    c = client(store, make_steps(fail_budget=True))
    task = send(c, hire_request())["result"]
    assert task["status"]["state"] == "completed"
    result = json.loads(result_text(task))
    assert result["job_status"] == "paused" and result["verdict"] is None
    assert result["reason"]


@pytest.mark.parametrize(
    "body, detail",
    [
        # Town's `a2a test` probe
        (
            {"request_id": "probe-1", "sku": "widget", "quantity": 2, "unit_price_cents": 1995},
            '"job"',
        ),
        ("not json", "not JSON"),
        ({"job": {"requester_id": "r"}, "request_id": "x"}, "not a valid JobPosting"),
    ],
)
def test_a_request_that_is_not_a_hire_fails_with_invalid_request(store, make_steps, body, detail):  # noqa: F811
    c = client(store, make_steps())
    task = send(c, body)["result"]
    assert task["status"]["state"] == "failed" and task["artifacts"] == []
    error = error_of(task)
    assert error["error"] == "invalid_request" and detail in error["detail"]


def test_pipeline_crash_fails_without_leaking_the_message(store, make_steps):  # noqa: F811
    def broken(*_):
        raise RuntimeError("secret detail from deep inside")

    c = client(store, make_steps(rubric=broken))
    task = send(c, hire_request())["result"]
    assert task["status"]["state"] == "failed"
    error = error_of(task)
    assert error["error"] == "pipeline_error" and error["detail"].endswith("RuntimeError")
    assert "secret detail" not in json.dumps(task)


def test_tasks_get_returns_a_stored_task_after_a_restart(store, make_steps):  # noqa: F811
    task = send(client(store, make_steps()), hire_request())["result"]
    restarted = client(store, make_steps())
    envelope = {"jsonrpc": "2.0", "id": 9, "method": "tasks/get", "params": {"id": task["id"]}}
    assert restarted.post("/", json=envelope).json()["result"] == task
    missing = {**envelope, "params": {"id": "task-000000000000"}}
    assert restarted.post("/", json=missing).json()["error"]["code"] == -32001
    sneaky = {**envelope, "params": {"id": "../../etc/passwd"}}
    assert restarted.post("/", json=sneaky).json()["error"]["code"] == -32001


def test_protocol_problems_are_json_rpc_errors(store, make_steps):  # noqa: F811
    c = client(store, make_steps())
    assert c.post("/", content=b"{nope").json()["error"]["code"] == -32700
    assert c.post("/", json=[1, 2]).json()["error"]["code"] == -32600
    other = {"jsonrpc": "2.0", "id": 1, "method": "message/stream", "params": {}}
    assert c.post("/", json=other).json()["error"]["code"] == -32601
    no_text = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {"message": {"parts": []}},
    }
    assert c.post("/", json=no_text).json()["error"]["code"] == -32602
