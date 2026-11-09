"""The Trial Runner over A2A (AGE-59, HW8 wire contracts §5)."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
import pytest

from contracts import (
    INVOICE_JSON_SCHEMA,
    AgentCard,
    ProviderTask,
    RegisteredProvider,
    RunPaused,
    TrialDoc,
    TrialPackage,
)
from core.runner.registry import a2a_url
from core.runner.trial_runner import TrialRunner, a2a_request
from data.synth import generate
from mocks.fake_providers import MOCKS, card, create_app

BASE = "http://providers"
DOCS = "http://docs.test"


def _a2a_provider(slug: str, price: float | None = 0.01) -> RegisteredProvider:
    c = AgentCard(
        slug=slug,
        name=slug,
        description="test",
        version="1",
        job_types=["invoice_extraction"],
        price_per_doc_usd=price,
        region="global",
    )
    return RegisteredProvider(
        card=c, base_url=BASE, transport="a2a", a2a_url=f"{BASE}/p/{slug}/a2a"
    )


@pytest.fixture
def trial() -> TrialPackage:
    docs = generate(layout="classic", n=3, seed=5, render=False)
    return TrialPackage(
        trial_id="trial-a2a",
        job_id="job-a2a",
        documents=[TrialDoc(doc_id=d.doc_id, source="synthetic", answer=d.truth) for d in docs],
        created_at=datetime(2026, 11, 9, tzinfo=UTC),
    )


def _task_body(request: httpx.Request) -> dict:
    envelope = json.loads(request.content)
    [part] = envelope["params"]["message"]["parts"]
    return json.loads(part["text"])


def _slug(request: httpx.Request) -> str:
    return request.url.path.split("/")[2]


def _rpc(request: httpx.Request, task: dict) -> httpx.Response:
    return httpx.Response(
        200, json={"jsonrpc": "2.0", "id": json.loads(request.content)["id"], "result": task}
    )


def _completed(*texts: str) -> dict:
    return {
        "kind": "task",
        "id": "t",
        "contextId": "c",
        "status": {"state": "completed"},
        "artifacts": [{"artifactId": "r", "parts": [{"kind": "text", "text": t} for t in texts]}],
    }


def _state(state: str, error: str | None = None) -> dict:
    status: dict = {"state": state}
    if error is not None:
        status["message"] = {
            "kind": "message",
            "role": "agent",
            "messageId": "m",
            "parts": [{"kind": "text", "text": json.dumps({"error": error})}],
        }
    return {"kind": "task", "id": "t", "contextId": "c", "status": status, "artifacts": []}


def _result_text(body: dict, **over) -> str:
    result = {
        "task_id": body["task_id"],
        "output": {"vendor_name": "X"},
        "cost_usd": 0.003,
        "provider_version": "1",
        "request_id": body["request_id"],
    } | over
    return json.dumps(result, sort_keys=True, separators=(",", ":"))


def _run(handler, trial, providers, **kw):
    kw = {"deadline_ms": 2000, "max_delay_s": 0} | kw
    runner = TrialRunner(DOCS, transport=httpx.MockTransport(handler), **kw)
    return asyncio.run(runner(trial, providers, "req-42"))


def _statuses(subs) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for s in subs:
        out.setdefault(s.provider, set()).add(s.status)
    return out


# --- the request (§3, §5) ---------------------------------------------------------------------


def test_message_send_goes_to_the_a2a_url_with_a_trailing_slash(trial):
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return _rpc(request, _completed(_result_text(_task_body(request))))

    subs = _run(handler, trial, [_a2a_provider("p1")])
    assert {s.status for s in subs} == {"ok"}
    assert {(r.method, str(r.url)) for r in seen} == {("POST", f"{BASE}/p/p1/a2a/")}
    assert sorted(_task_body(r)["task_id"] for r in seen) == sorted(s.task_id for s in subs)
    for r in seen:
        envelope = json.loads(r.content)
        assert (envelope["jsonrpc"], envelope["method"]) == ("2.0", "message/send")
        body = _task_body(r)
        assert body["request_id"] == body["nonce"] == body["task_id"]
        task = ProviderTask.model_validate(
            {k: v for k, v in body.items() if k not in ("request_id", "nonce")}
        )
        assert task.requester_id == "req-42"
        assert task.output_schema == INVOICE_JSON_SCHEMA
        assert task.offered_price == 0.01
        assert task.document_url.startswith(f"{DOCS}/d/")


def test_unknown_card_price_is_offered_as_zero():
    task = ProviderTask(
        task_id="abc",
        requester_id="r",
        document_url=f"{DOCS}/d/0123456789ab.pdf",
        output_schema={},
        deadline_ms=1000,
        offered_price=0.0,
    )
    body = json.loads(a2a_request(task)["params"]["message"]["parts"][0]["text"])
    assert body == task.model_dump(mode="json") | {"request_id": "abc", "nonce": "abc"}


def test_unknown_price_sends_offered_price_zero(trial):
    offered: list[float] = []

    def handler(request):
        body = _task_body(request)
        offered.append(body["offered_price"])
        return _rpc(request, _completed(_result_text(body)))

    _run(handler, trial, [_a2a_provider("plain", price=None)])
    assert set(offered) == {0.0}


# --- outcome mapping (§5) ---------------------------------------------------------------------


def test_a2a_ok_failed_timeout_and_other_outcomes(trial):
    async def handler(request):
        body = _task_body(request)
        match _slug(request):
            case "ok":
                return _rpc(request, _completed(_result_text(body)))
            case "failed":
                return _rpc(request, _state("failed", "internal"))
            case "deadline":  # the provider gave up itself: still a failed task → error
                return _rpc(request, _state("failed", "deadline_exceeded"))
            case "slow":
                await asyncio.sleep(5)
                return _rpc(request, _completed(_result_text(body)))
            case "rejected":
                return _rpc(request, _state("rejected"))
            case "working":
                return _rpc(request, _state("working"))
            case "two-parts":
                return _rpc(request, _completed(_result_text(body), _result_text(body)))
            case "not-a-result":
                return _rpc(request, _completed(json.dumps({"task_id": body["task_id"]})))
            case "not-json":
                return _rpc(request, _completed("hello"))
            case "rpc-error":
                return httpx.Response(
                    200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "x"}}
                )
            case "http-500":
                return httpx.Response(500)
            case "garbage":
                return httpx.Response(200, content=b"<html>")
        raise AssertionError(request.url)

    slugs = [
        "ok",
        "failed",
        "deadline",
        "slow",
        "rejected",
        "working",
        "two-parts",
        "not-a-result",
        "not-json",
        "rpc-error",
        "http-500",
        "garbage",
    ]
    subs = _run(handler, trial, [_a2a_provider(s) for s in slugs], deadline_ms=300)
    expected = {s: "error" for s in slugs} | {"ok": "ok", "slow": "timeout"}
    assert _statuses(subs) == {f"{s}@1": {status} for s, status in expected.items()}
    for s in subs:
        if s.status == "ok":
            assert (s.output, s.cost_usd) == ({"vendor_name": "X"}, 0.003)
        else:
            assert s.output is None and s.cost_usd == 0.0


def test_a2a_connection_error_is_an_error(trial):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    assert {s.status for s in _run(handler, trial, [_a2a_provider("down")])} == {"error"}


def test_a2a_budget_exhausted_pauses_the_run_like_a_402(trial):
    def handler(request):
        if _slug(request) == "broke":
            return _rpc(request, _state("failed", "budget_exhausted"))
        return _rpc(request, _completed(_result_text(_task_body(request))))

    providers = [_a2a_provider("broke"), _a2a_provider("fine")]
    with pytest.raises(RunPaused) as exc:
        _run(handler, trial, providers, max_concurrency=1)
    assert exc.value.reason == "budget_exhausted"
    subs = exc.value.submissions
    assert len(subs) == 2 * len(trial.documents)
    broke = [s for s in subs if s.provider == "broke@1"]
    assert {s.status for s in broke} == {"not_run"}
    assert all(s.latency_ms is None and s.cost_usd is None for s in broke)


def test_a2a_url_missing_is_an_error(trial):
    p = _a2a_provider("p1").model_copy(update={"a2a_url": None})
    assert {s.status for s in _run(lambda r: httpx.Response(500), trial, [p])} == {"error"}


# --- transport parity against mocks/fake_providers -------------------------------------------


@pytest.fixture
def fixed_trial(tmp_path) -> tuple[TrialPackage, Path]:
    """One fixed TrialPackage: seeded synthetic docs, so fixed doc_ids and truth files."""
    docs = generate(layout="classic", n=4, seed=11, render=False, truth_dir=tmp_path)
    trial = TrialPackage(
        trial_id="trial-parity",
        job_id="job-parity",
        documents=[TrialDoc(doc_id=d.doc_id, source="synthetic", answer=d.truth) for d in docs],
        created_at=datetime(2026, 11, 9, tzinfo=UTC),
    )
    return trial, tmp_path


def test_http_and_a2a_give_identical_submissions(fixed_trial):
    trial, truth_dir = fixed_trial

    def run(transport: Literal["http", "a2a"]):
        app = create_app(truth_dir=truth_dir, base_url=BASE)  # fresh app: no A2A reply cache
        providers = [
            RegisteredProvider(
                card=card(s),
                base_url=BASE,
                transport=transport,
                a2a_url=a2a_url(BASE, s) if transport == "a2a" else None,
            )
            for s in MOCKS
        ]
        runner = TrialRunner(
            DOCS, deadline_ms=500, max_delay_s=0, transport=httpx.ASGITransport(app=app)
        )
        subs = asyncio.run(runner(trial, providers, "req-parity"))
        return [(s.provider, s.doc_id, s.status, s.output, s.cost_usd) for s in subs]

    http, a2a = run("http"), run("a2a")
    assert a2a == http
    statuses = {p: st for p, _, st, _, _ in http}
    assert statuses["mock-d@1"] == "timeout" and statuses["mock-a@1"] == "ok"
    assert len(http) == len(MOCKS) * len(trial.documents)
