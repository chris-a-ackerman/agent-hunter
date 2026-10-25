"""Trial Runner (AGE-15): sends every trial document to every selected provider over HTTP.

Each (provider, doc) pair is one `ProviderTask` (plan §9.2), built exactly as for a real job:
the posting's requester ID, `{DOCS_BASE_URL}/d/{doc_id}.pdf` (D17), the invoice schema, the
deadline and the card's list price. No other fields, headers or query params, and no trial marker.

Pacing (D23): tasks start in a shuffled order, under a concurrency cap, each after a random
0–1.5 s delay; the order and delays come from an RNG seeded with the trial ID.

Outcomes (D18): 200 + parseable `ProviderResult` → ok (raw output kept, the Scorer judges it);
no answer within the deadline → timeout; 402 + `{"error": "budget_exhausted"}` → stop launching
tasks, let in-flight ones finish, mark the rest not_run and raise `RunPaused`; anything else
→ error.

    DOCS_BASE_URL      where providers fetch `/d/{doc_id}.pdf` (default http://localhost:8200)
    TASK_DEADLINE_MS   per-task deadline sent to providers and enforced here (default 60000)
    MAX_CONCURRENCY    tasks in flight at once (default 24: the 7 × 6 demo trial takes ~30 s and
                       peaks at ~50 provider calls/min, under Parley's 90/min)
"""

import asyncio
import os
import random
import time
import uuid
from collections import Counter

import httpx

from contracts import (
    INVOICE_JSON_SCHEMA,
    ProviderError,
    ProviderResult,
    ProviderTask,
    RegisteredProvider,
    RunEvent,
    RunPaused,
    Submission,
    TrialDoc,
    TrialPackage,
)
from contracts.provider import SubmissionStatus
from contracts.steps import OnEvent

STEP = "trial_runner"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, "").strip() or default


class TrialRunner:
    """A configured Trial Runner; satisfies `contracts.steps.RunTrial`.

    `max_delay_s` is the D23 pacing delay (0 turns it off, for tests); `transport` lets tests
    route requests to an in-process app or an `httpx.MockTransport`.
    """

    def __init__(
        self,
        docs_base_url: str,
        *,
        deadline_ms: int = 60_000,
        max_concurrency: int = 8,
        max_delay_s: float = 1.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if deadline_ms <= 0 or max_concurrency <= 0 or max_delay_s < 0:
            raise ValueError("deadline_ms and max_concurrency must be > 0, max_delay_s >= 0")
        self.docs_base_url = docs_base_url.rstrip("/")
        self.deadline_ms = deadline_ms
        self.max_concurrency = max_concurrency
        self.max_delay_s = max_delay_s
        self.transport = transport

    @classmethod
    def from_env(cls) -> "TrialRunner":
        return cls(
            _env("DOCS_BASE_URL", "http://localhost:8200"),
            deadline_ms=int(_env("TASK_DEADLINE_MS", "60000")),
            max_concurrency=int(_env("MAX_CONCURRENCY", "24")),
        )

    async def __call__(
        self,
        trial: TrialPackage,
        providers: list[RegisteredProvider],
        requester_id: str,
        on_event: OnEvent | None = None,
    ) -> list[Submission]:
        pairs = [(p, d) for p in providers for d in trial.documents]
        rng = random.Random(trial.trial_id)
        order = list(range(len(pairs)))
        rng.shuffle(order)
        delays = {i: rng.uniform(0, self.max_delay_s) for i in order}

        sem = asyncio.Semaphore(self.max_concurrency)
        paused = asyncio.Event()
        remaining = Counter(p.provider_id for p, _ in pairs)
        statuses: dict[str, Counter[str]] = {p.provider_id: Counter() for p in providers}

        def emit(kind: str, detail: dict) -> None:
            if on_event is not None:
                on_event(RunEvent(job_id=trial.job_id, step=STEP, kind=kind, detail=detail))

        async def one(client: httpx.AsyncClient, i: int) -> Submission:
            p, doc = pairs[i]
            async with sem:
                if delays[i] and not paused.is_set():
                    await asyncio.sleep(delays[i])
                if paused.is_set():
                    sub = _not_run(doc, p)
                else:
                    sub = await self._send(client, p, doc, requester_id, paused)
            emit(
                "task_done",
                {
                    "provider": sub.provider,
                    "doc_id": sub.doc_id,
                    "status": sub.status,
                    "latency_ms": sub.latency_ms,
                },
            )
            statuses[sub.provider][sub.status] += 1
            remaining[sub.provider] -= 1
            if remaining[sub.provider] == 0:
                counts = statuses[sub.provider]
                emit(
                    "provider_done",
                    {"provider": sub.provider}
                    | {s: counts[s] for s in ("ok", "timeout", "error", "not_run")},
                )
            return sub

        timeout = httpx.Timeout(self.deadline_ms / 1000)
        async with httpx.AsyncClient(transport=self.transport, timeout=timeout) as client:
            # Tasks are created in shuffled order; the semaphore admits them first-come.
            done = await asyncio.gather(*(one(client, i) for i in order))
        by_index = dict(zip(order, done, strict=True))
        subs = [by_index[i] for i in range(len(pairs))]
        if paused.is_set():
            raise RunPaused(subs)
        return subs

    async def _send(
        self,
        client: httpx.AsyncClient,
        p: RegisteredProvider,
        doc: TrialDoc,
        requester_id: str,
        paused: asyncio.Event,
    ) -> Submission:
        task = ProviderTask(
            task_id=uuid.uuid4().hex,
            requester_id=requester_id,
            document_url=f"{self.docs_base_url}/d/{doc.doc_id}.pdf",
            output_schema=INVOICE_JSON_SCHEMA,
            deadline_ms=self.deadline_ms,
            offered_price=p.card.price_per_doc_usd,
        )
        base = {"task_id": task.task_id, "doc_id": doc.doc_id, "provider": p.provider_id}

        def failed(status: SubmissionStatus, latency_ms: int) -> Submission:
            return Submission(
                **base, status=status, output=None, latency_ms=latency_ms, cost_usd=0.0
            )

        t0 = time.monotonic()
        try:
            # httpx timeouts are per phase; asyncio.timeout bounds the whole exchange.
            async with asyncio.timeout(self.deadline_ms / 1000):
                resp = await client.post(p.tasks_url, json=task.model_dump())
        except (TimeoutError, httpx.TimeoutException):
            return failed("timeout", _ms(t0))
        except httpx.HTTPError:
            return failed("error", _ms(t0))
        latency = _ms(t0)

        if resp.status_code == 402 and _error_code(resp) == "budget_exhausted":
            paused.set()
            return _not_run(doc, p, task.task_id)
        if resp.status_code != 200:
            return failed("error", latency)
        try:
            result = ProviderResult.model_validate_json(resp.content)
        except ValueError:
            return failed("error", latency)
        return Submission(
            **base, status="ok", output=result.output, latency_ms=latency, cost_usd=result.cost_usd
        )


async def run_trial(
    trial: TrialPackage,
    providers: list[RegisteredProvider],
    requester_id: str,
    on_event: OnEvent | None = None,
) -> list[Submission]:
    """`contracts.steps.RunTrial`, configured from the environment (see module docstring)."""
    return await TrialRunner.from_env()(trial, providers, requester_id, on_event)


def _ms(t0: float) -> int:
    return round((time.monotonic() - t0) * 1000)


def _error_code(resp: httpx.Response) -> str | None:
    try:
        return ProviderError.model_validate_json(resp.content).error
    except ValueError:
        return None


def _not_run(doc: TrialDoc, p: RegisteredProvider, task_id: str | None = None) -> Submission:
    return Submission(
        task_id=task_id or uuid.uuid4().hex,
        doc_id=doc.doc_id,
        provider=p.provider_id,
        status="not_run",
        output=None,
        latency_ms=None,
        cost_usd=None,
    )
