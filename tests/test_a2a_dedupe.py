"""Idempotent hire requests at the A2A door (AGE-48). Town resends the byte-identical body for
`duplicate_request` and passes only if the second reply's text part is byte-identical to the
first. Written before the fix: these fail on the pass-through door (e356852) and pass after it.
The concurrent-duplicate case is left to the fix, since it depends on how runs are locked."""

import asyncio
import json
import re
import time

import httpx

from core.a2a import create_app, terms_digest
from core.pipeline import stubs
from core.store import A2ARequest
from tests.test_a2a import client, env, error_of, hire_request, result_text, send  # noqa: F401  (env: autouse)
from tests.test_pipeline.conftest import load_example, make_steps, store  # noqa: F401  (fixtures)


def counting_steps(make_steps, delay_s: float = 0.0):  # noqa: F811
    """Steps whose Job Reviewer counts calls: one call per pipeline run. `delay_s` keeps a run
    going long enough for a concurrent duplicate to arrive mid-run."""
    calls = {"n": 0}

    def review(*args, **kwargs):
        calls["n"] += 1
        time.sleep(delay_s)  # the reviewer runs in a worker thread, so the loop keeps serving
        return stubs.review_job_stub(*args, **kwargs)

    return make_steps(review=review), calls


def test_identical_retry_returns_the_stored_reply_without_a_second_run(store, make_steps):  # noqa: F811
    steps, calls = counting_steps(make_steps)
    c = client(store, steps)
    first = send(c, hire_request())["result"]
    second = send(c, hire_request())["result"]
    assert second["status"]["state"] == "completed"
    assert result_text(second) == result_text(first)  # byte-identical: Town compares digests
    assert json.loads(result_text(second))["job_id"] == json.loads(result_text(first))["job_id"]
    assert calls["n"] == 1  # no second job, trial or spend


def test_same_request_id_with_changed_terms_is_rejected(store, make_steps):  # noqa: F811
    steps, calls = counting_steps(make_steps)
    c = client(store, steps)
    send(c, hire_request())
    changed = hire_request() | {"nonce": "different"}  # terms = whole body minus request_id
    task = send(c, changed)["result"]
    assert task["status"]["state"] == "rejected"
    assert "different terms" in json.dumps(task["status"])
    assert calls["n"] == 1


def test_dedupe_survives_a_restart_of_the_door(store, make_steps):  # noqa: F811
    steps, calls = counting_steps(make_steps)
    first = send(client(store, steps), hire_request())["result"]
    restarted = client(store, steps)  # same store, new app: the mapping must be persisted
    second = send(restarted, hire_request())["result"]
    assert result_text(second) == result_text(first)
    assert calls["n"] == 1


def envelope(body: dict, rpc_id: int) -> dict:
    message = {
        "role": "user",
        "messageId": f"m-{rpc_id}",
        "parts": [{"kind": "text", "text": json.dumps(body)}],
    }
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "message/send",
        "params": {"message": message},
    }


def test_concurrent_duplicate_waits_for_the_first_run(store, make_steps):  # noqa: F811
    steps, calls = counting_steps(make_steps, delay_s=0.3)
    app = create_app(store, build_steps=lambda *_: steps, serve_docs=False)

    async def both() -> list[dict]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://door") as c:
            replies = await asyncio.gather(
                *(c.post("/", json=envelope(hire_request(), i)) for i in (1, 2))
            )
        return [r.json()["result"] for r in replies]

    first, second = asyncio.run(both())
    assert first["status"]["state"] == second["status"]["state"] == "completed"
    assert result_text(first) == result_text(second)
    assert first["id"] == second["id"]  # the same task, not a second one
    assert calls["n"] == 1


def test_a_request_left_running_by_a_dead_door_is_interrupted(store, make_steps):  # noqa: F811
    steps, calls = counting_steps(make_steps)
    body = hire_request()
    job_id = store.create_job(load_example("invoice_clean"))  # the dead door's job, never run
    store.save_a2a_request(
        A2ARequest(
            request_id=body["request_id"],
            terms_digest=terms_digest(body),
            state="running",
            job_id=job_id,
            task_id="task-000000000001",
        )
    )
    c = client(store, steps)
    task = send(c, body)["result"]
    assert task["status"]["state"] == "failed" and task["artifacts"] == []
    assert error_of(task)["error"] == "interrupted"
    assert send(c, body)["result"] == task  # saved: later retries get the same reply
    assert calls["n"] == 0  # no second job started silently


def test_a_request_left_running_after_its_job_finished_returns_the_result(store, make_steps):  # noqa: F811
    steps, calls = counting_steps(make_steps)
    body = hire_request()
    c = client(store, steps)
    job_id = json.loads(result_text(send(c, body)["result"]))["job_id"]
    # The door died after the pipeline finished but before the reply was saved.
    seen = store.get_a2a_request(body["request_id"])
    store.save_a2a_request(seen.model_copy(update={"state": "running", "task": None}))
    task = send(client(store, steps), body)["result"]
    assert task["status"]["state"] == "completed"
    assert json.loads(result_text(task))["job_id"] == job_id
    assert calls["n"] == 1


def test_a_body_without_a_request_id_is_not_deduped(store, make_steps):  # noqa: F811
    steps, calls = counting_steps(make_steps)
    c = client(store, steps)
    body = {k: v for k, v in hire_request().items() if k != "request_id"}
    first, second = (json.loads(result_text(send(c, body)["result"])) for _ in range(2))
    assert first["job_id"] != second["job_id"] and calls["n"] == 2


def test_dedupe_records_are_keyed_safely(store):  # noqa: F811
    assert store.get_a2a_request("../../etc/passwd") is None
    req = A2ARequest(request_id="../x", terms_digest="d", state="running", task_id="task-1")
    store.save_a2a_request(req)
    assert store.get_a2a_request("../x") == req
    [path] = store.root.rglob("*.json")
    assert path.parent == store.root / "a2a_requests"
    assert re.fullmatch(r"[0-9a-f]{64}\.json", path.name)  # hashed: the ID never names a path
