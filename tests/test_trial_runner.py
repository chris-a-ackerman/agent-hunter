import asyncio
import json
import time
from datetime import UTC, datetime

import httpx
import pytest

from contracts import (
    INVOICE_JSON_SCHEMA,
    AgentCard,
    ProviderTask,
    RegisteredProvider,
    RunEvent,
    RunPaused,
    Submission,
    TrialDoc,
    TrialPackage,
)
from core.runner.trial_runner import TrialRunner, run_trial
from data.synth import generate
from mocks.fake_providers import MOCKS, card, create_app

BASE = "http://providers"
DOCS = "http://docs.test"


def _provider(slug: str, version: str = "1", price: float = 0.01) -> RegisteredProvider:
    c = AgentCard(
        slug=slug,
        name=slug,
        description="test",
        version=version,
        job_types=["invoice_extraction"],
        price_per_doc_usd=price,
        region="global",
    )
    return RegisteredProvider(card=c, base_url=BASE)


@pytest.fixture
def trial_and_truth(tmp_path):
    docs = generate(layout="classic", n=6, seed=3, render=False, truth_dir=tmp_path)
    trial = TrialPackage(
        trial_id="trial-test",
        job_id="job-test",
        documents=[TrialDoc(doc_id=d.doc_id, source="synthetic", answer=d.truth) for d in docs],
        created_at=datetime.now(UTC),
    )
    return trial, tmp_path


@pytest.fixture
def trial(trial_and_truth) -> TrialPackage:
    return trial_and_truth[0]


def _result(task: dict, **over) -> dict:
    return {
        "task_id": task["task_id"],
        "output": {"vendor_name": "X"},
        "cost_usd": 0.002,
        "provider_version": "1",
    } | over


def _runner(handler, **kw) -> TrialRunner:
    kw = {"deadline_ms": 2000, "max_delay_s": 0} | kw
    return TrialRunner(DOCS, transport=httpx.MockTransport(handler), **kw)


def _run(runner, trial, providers, events=None):
    return asyncio.run(
        runner(trial, providers, "req-42", events.append if events is not None else None)
    )


def _slug(request: httpx.Request) -> str:
    return request.url.path.split("/")[2]


# --- outcomes (D18) ---------------------------------------------------------------------------


def test_outcomes_ok_timeout_500_and_garbage_are_recorded(trial):
    async def handler(request: httpx.Request) -> httpx.Response:
        task = json.loads(request.content)
        match _slug(request):
            case "ok":
                return httpx.Response(200, json=_result(task))
            case "invalid":  # parses as a ProviderResult, isn't an Invoice: still ok
                return httpx.Response(200, json=_result(task, output={"text": "not json"}))
            case "slow":
                await asyncio.sleep(5)
                return httpx.Response(200, json=_result(task))
            case "boom":
                return httpx.Response(500, json={"error": "internal"})
            case "garbage":
                return httpx.Response(200, content=b"<html>nope</html>")
            case "missing":  # 200 JSON that isn't a ProviderResult
                return httpx.Response(200, json={"task_id": task["task_id"]})
        raise AssertionError(request.url)

    slugs = ["ok", "invalid", "slow", "boom", "garbage", "missing"]
    providers = [_provider(s) for s in slugs]
    t0 = time.monotonic()
    subs = _run(_runner(handler, deadline_ms=300), trial, providers)
    assert time.monotonic() - t0 < 3

    assert len(subs) == len(slugs) * len(trial.documents)
    status = {s.provider: {x.status for x in subs if x.provider == s.provider} for s in subs}
    assert status == {
        "ok@1": {"ok"},
        "invalid@1": {"ok"},
        "slow@1": {"timeout"},
        "boom@1": {"error"},
        "garbage@1": {"error"},
        "missing@1": {"error"},
    }
    for s in subs:
        Submission.model_validate(s.model_dump())
        assert s.latency_ms is not None and s.latency_ms >= 0
        if s.status == "ok":
            assert s.cost_usd == 0.002
        else:
            assert s.output is None and s.cost_usd == 0.0
    assert {s.provider: s.output for s in subs if s.status == "ok"} == {
        "ok@1": {"vendor_name": "X"},
        "invalid@1": {"text": "not json"},
    }
    timeouts = [s for s in subs if s.status == "timeout"]
    assert all(250 <= s.latency_ms < 1500 for s in timeouts)


def test_connection_error_is_an_error_not_a_crash(trial):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    subs = _run(_runner(handler), trial, [_provider("down")])
    assert {s.status for s in subs} == {"error"}


def test_submissions_come_back_in_provider_then_doc_order(trial):
    def handler(request):
        return httpx.Response(200, json=_result(json.loads(request.content)))

    providers = [_provider(s) for s in ("p1", "p2", "p3")]
    subs = _run(_runner(handler, max_delay_s=0.01), trial, providers)
    assert [(s.provider, s.doc_id) for s in subs] == [
        (p.provider_id, d.doc_id) for p in providers for d in trial.documents
    ]


# --- 402 budget_exhausted → RunPaused ---------------------------------------------------------


def test_402_budget_exhausted_pauses_with_not_run_tasks(trial):
    sent: list[str] = []
    fourth_sent: list[asyncio.Event] = []  # created lazily, inside the runner's event loop

    async def handler(request):
        if not fourth_sent:
            fourth_sent.append(asyncio.Event())
        sent.append(json.loads(request.content)["task_id"])
        if len(sent) == 4:
            fourth_sent[0].set()
        if len(sent) == 3:  # answer 402 only once task 4 is in flight
            await fourth_sent[0].wait()
            return httpx.Response(402, json={"error": "budget_exhausted"})
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=_result(json.loads(request.content)))

    providers = [_provider(s) for s in ("p1", "p2", "p3")]
    with pytest.raises(RunPaused) as exc:
        _run(_runner(handler, max_concurrency=2), trial, providers)

    subs = exc.value.submissions
    assert exc.value.reason == "budget_exhausted"
    assert len(subs) == 3 * len(trial.documents)
    # Tasks 1, 2 and 4 (in flight when the 402 came) finish; no new task is sent after it.
    assert len(sent) == 4
    status = {s.task_id: s.status for s in subs}
    assert [status[t] for t in sent] == ["ok", "ok", "not_run", "ok"]
    assert sum(s.status == "not_run" for s in subs) == 3 * len(trial.documents) - 3
    for s in subs:
        if s.status == "not_run":
            assert s.latency_ms is None and s.cost_usd is None and s.output is None


def test_402_with_another_error_is_just_an_error(trial):
    def handler(request):
        return httpx.Response(402, json={"error": "payment_required"})

    subs = _run(_runner(handler), trial, [_provider("p1")])
    assert {s.status for s in subs} == {"error"}


# --- the request is a real job's request (plan §9.2) -------------------------------------------


def test_request_body_has_exactly_the_six_task_fields(trial):
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=_result(json.loads(request.content)))

    providers = [_provider("p1", price=0.007), _provider("p2", version="2.0", price=0.003)]
    subs = _run(_runner(handler, deadline_ms=4321), trial, providers)

    assert len(seen) == 2 * len(trial.documents)
    doc_ids = {d.doc_id for d in trial.documents}
    for req in seen:
        body = json.loads(req.content)
        assert set(body) == {
            "task_id",
            "requester_id",
            "document_url",
            "output_schema",
            "deadline_ms",
            "offered_price",
        }
        assert set(body) == set(ProviderTask.model_fields)
        slug = _slug(req)
        assert req.method == "POST" and str(req.url) == f"{BASE}/p/{slug}/tasks"
        assert req.url.query == b""
        assert body["requester_id"] == "req-42"
        assert body["output_schema"] == INVOICE_JSON_SCHEMA
        assert body["deadline_ms"] == 4321
        assert body["offered_price"] == {"p1": 0.007, "p2": 0.003}[slug]
        prefix = f"{DOCS}/d/"
        assert body["document_url"].startswith(prefix) and body["document_url"].endswith(".pdf")
        assert body["document_url"][len(prefix) : -4] in doc_ids
        # No custom headers: only what httpx sends for any JSON POST.
        assert {k.lower() for k in req.headers} <= {
            "host",
            "accept",
            "accept-encoding",
            "connection",
            "user-agent",
            "content-length",
            "content-type",
        }
        assert "trial" not in json.dumps(body).lower()

    task_ids = [json.loads(r.content)["task_id"] for r in seen]
    assert len(set(task_ids)) == len(task_ids)
    assert {s.task_id for s in subs} == set(task_ids)


def test_tasks_are_shuffled_and_paced_deterministically(trial):
    def order(max_delay_s: float) -> list[tuple[str, str]]:
        seen: list[tuple[str, str]] = []

        def handler(request):
            body = json.loads(request.content)
            seen.append((_slug(request), body["document_url"]))
            return httpx.Response(200, json=_result(body))

        providers = [_provider(s) for s in ("p1", "p2")]
        _run(_runner(handler, max_concurrency=1, max_delay_s=max_delay_s), trial, providers)
        return seen

    canonical = [(p, f"{DOCS}/d/{d.doc_id}.pdf") for p in ("p1", "p2") for d in trial.documents]
    first = order(0)
    assert sorted(first) == sorted(canonical) and first != canonical
    assert order(0) == first  # seeded by the trial ID

    t0 = time.monotonic()
    order(0.05)
    assert time.monotonic() - t0 >= 0.05  # the D23 delays are applied


def test_concurrency_cap_is_respected(trial):
    in_flight = peak = 0

    async def handler(request):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return httpx.Response(200, json=_result(json.loads(request.content)))

    providers = [_provider(f"p{i}") for i in range(4)]
    _run(_runner(handler, max_concurrency=3), trial, providers)
    assert peak == 3


# --- events -----------------------------------------------------------------------------------


def test_task_done_and_provider_done_events(trial):
    def handler(request):
        if _slug(request) == "bad":
            return httpx.Response(500)
        return httpx.Response(200, json=_result(json.loads(request.content)))

    events: list[RunEvent] = []
    _run(_runner(handler), trial, [_provider("good"), _provider("bad")], events)

    n = len(trial.documents)
    assert all(e.job_id == "job-test" and e.step == "trial_runner" for e in events)
    done = [e for e in events if e.kind == "task_done"]
    assert len(done) == 2 * n
    assert set(done[0].detail) == {"provider", "doc_id", "status", "latency_ms", "cost_usd"}
    cost = {e.detail["provider"]: e.detail["cost_usd"] for e in done}
    assert cost == {"good@1": 0.002, "bad@1": 0.0}
    finished = {e.detail["provider"]: e.detail for e in events if e.kind == "provider_done"}
    assert finished == {
        "good@1": {"provider": "good@1", "ok": n, "timeout": 0, "error": 0, "not_run": 0},
        "bad@1": {"provider": "bad@1", "ok": 0, "timeout": 0, "error": n, "not_run": 0},
    }
    # provider_done comes after that provider's last task_done.
    for p in finished:
        last_task = max(
            i for i, e in enumerate(events) if e.kind == "task_done" and e.detail["provider"] == p
        )
        provider_done = next(
            i
            for i, e in enumerate(events)
            if e.kind == "provider_done" and e.detail["provider"] == p
        )
        assert provider_done == last_task + 1


# --- against mocks/fake_providers --------------------------------------------------------------


def _mock_providers() -> list[RegisteredProvider]:
    """The five mocks plus two more registrations, for the plan's 7 providers."""
    providers = [RegisteredProvider(card=card(s), base_url=BASE) for s in MOCKS]
    for slug in ("mock-a", "mock-b"):
        c = card(slug).model_copy(update={"version": "2"})
        providers.append(RegisteredProvider(card=c, base_url=BASE))
    return providers


def test_seven_providers_six_docs_against_the_mocks_under_45s(trial_and_truth):
    trial, truth_dir = trial_and_truth
    app = create_app(truth_dir=truth_dir)
    runner = TrialRunner(
        DOCS, deadline_ms=3000, transport=httpx.ASGITransport(app=app)
    )  # default concurrency and D23 pacing
    events: list[RunEvent] = []

    t0 = time.monotonic()
    subs = _run(runner, trial, _mock_providers(), events)
    elapsed = time.monotonic() - t0

    assert elapsed < 45
    assert len(subs) == 7 * 6
    by_provider: dict[str, set[str]] = {}
    for s in subs:
        by_provider.setdefault(s.provider, set()).add(s.status)
    assert by_provider["mock-d@1"] == {"timeout"}
    assert all(v == {"ok"} for k, v in by_provider.items() if k != "mock-d@1")
    ok = [s for s in subs if s.status == "ok"]
    assert all(s.cost_usd == MOCKS[s.provider.split("@")[0]].cost_usd for s in ok)
    assert sum(e.kind == "provider_done" for e in events) == 7


def test_mock_fail_budget_pauses_the_run(trial_and_truth):
    trial, truth_dir = trial_and_truth
    app = create_app(truth_dir=truth_dir, fail_budget=True)
    runner = TrialRunner(DOCS, max_delay_s=0, transport=httpx.ASGITransport(app=app))
    with pytest.raises(RunPaused) as exc:
        _run(runner, trial, _mock_providers())
    assert {s.status for s in exc.value.submissions} == {"not_run"}
    assert len(exc.value.submissions) == 42


# --- configuration ----------------------------------------------------------------------------


def test_run_trial_reads_the_environment(monkeypatch):
    monkeypatch.setenv("DOCS_BASE_URL", "http://docs.example/")
    monkeypatch.setenv("TASK_DEADLINE_MS", "1234")
    monkeypatch.setenv("MAX_CONCURRENCY", "3")
    r = TrialRunner.from_env()
    assert (r.docs_base_url, r.deadline_ms, r.max_concurrency, r.max_delay_s) == (
        "http://docs.example",
        1234,
        3,
        1.5,
    )
    monkeypatch.delenv("TASK_DEADLINE_MS")
    monkeypatch.delenv("MAX_CONCURRENCY")
    assert (TrialRunner.from_env().deadline_ms, TrialRunner.from_env().max_concurrency) == (
        60_000,
        24,
    )
    assert asyncio.iscoroutinefunction(run_trial)


@pytest.mark.parametrize("kw", [{"deadline_ms": 0}, {"max_concurrency": 0}, {"max_delay_s": -1}])
def test_bad_settings_are_rejected(kw):
    with pytest.raises(ValueError):
        TrialRunner(DOCS, **kw)
