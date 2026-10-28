"""The demo web app (AGE-32): one page that runs an example job and shows each step live.

    uv run python -m core.web [--port 8300]        then open http://localhost:8300

Serves `apps/web/index.html` and the small JSON API it polls:

    GET  /api/config         the example jobs and the rehearsal toggles' defaults
    POST /api/runs           {"example": "inv-clean", "mock": false, "scorer": "real"} → job_id
    GET  /api/runs/{job_id}  everything the page shows: step states and timings, what each step
                             produced, the provider lanes, scores, verdict and costs

Like `run_job`, the pipeline runs in-process (one job at a time, in its own thread), the doc
server is started on DOCS_BASE_URL if nothing answers there, and "Mock providers" uses run_job's
`--mock` wiring. It's a separate app from `core.api` on purpose: the page shows the trial
documents' vendors and totals, which come from the answer key, and `core.api` (which providers
reach for documents) never serves the answer key.
"""

import asyncio
import dataclasses
import inspect
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from contracts import (
    JobPosting,
    JobSpec,
    JobStatus,
    RegisteredProvider,
    Rubric,
    RunEvent,
    ScoringConfig,
    TrialPackage,
)
from core.pipeline.config import provider_slugs
from core.pipeline.runner import Steps, run_pipeline
from core.runner.candidate_finder import rejection_reason
from core.store import JobNotFound, JobRecord, JsonStore, Store
from examples.make_examples import EXAMPLES_DIR

log = logging.getLogger(__name__)

INDEX_HTML = Path(__file__).resolve().parent.parent / "apps" / "web" / "index.html"

STEP_NAMES = ("review", "rubric", "trial", "eval", "candidates", "trial_runner", "score", "rank")

# Everything a run sets in the environment; restored before each run, so a "Mock providers"
# run doesn't leak into the next one.
ENV_KEYS = (
    "SCORER",
    "PROVIDERS_BASE_URL",
    "PROVIDER_SLUGS",
    "TASK_DEADLINE_MS",
    "SAMPLES_DIR",
)


@dataclasses.dataclass(frozen=True)
class Example:
    key: str
    file: str
    title: str
    short: str


EXAMPLES = (
    Example(
        "inv-clean",
        "invoice_clean.json",
        "Invoice extraction (clean PDFs)",
        "Digital PDFs with short tables",
    ),
    Example(
        "inv-long",
        "invoice_long.json",
        "Invoice extraction (long invoices)",
        "Multi-page invoices, many line items",
    ),
    Example(
        "inv-mixed",
        "invoice_mixed.json",
        "Invoice extraction (scans, no tax)",
        "Scanned and digital invoices, often no tax line",
    ),
    Example(
        "cover",
        "reject_unscorable.json",
        "Cover letter (should be rejected)",
        "Subjective writing; the reviewer should stop it",
    ),
)


def load_posting(example: Example) -> JobPosting:
    return JobPosting.model_validate_json((EXAMPLES_DIR / example.file).read_text("utf-8"))


def posting_view(p: JobPosting) -> dict[str, Any]:
    return {
        "description": p.description,
        "wants": p.output_description,
        "samples": len(p.samples),
        "samples_with_answers": sum(s.answer is not None for s in p.samples),
        "n_real_docs": p.n_real_docs,
        "job_budget_usd": p.job_budget_usd,
        "trial_budget_usd": p.trial_budget_usd,
        "region": p.region or "any",
    }


# --- one run ----------------------------------------------------------------------------------


class LiveRun:
    """What a run's `RunEvent`s and step taps reported, in seconds since the run started.
    Events arrive on the pipeline's thread, `view` is called from the API's, hence the lock."""

    def __init__(self, job_id: str, example: str, mock: bool, scorer: str) -> None:
        self.job_id, self.example, self.mock, self.scorer = job_id, example, mock, scorer
        self.lock = threading.Lock()
        self.t0 = time.monotonic()
        self.ended: float | None = None
        self.started: dict[str, float] = {}
        self.finished: dict[str, float] = {}
        self.stopped: tuple[str, float] | None = None  # (step, at)
        self.error: str | None = None
        self.slugs: list[str] = []  # the configured registry, to name providers left out
        self.registry: list[RegisteredProvider] | None = None
        self.kept: list[RegisteredProvider] | None = None
        self.answer_keys: int | None = None
        # provider → doc_id → {status, at (s since the trial step started), cost_usd}
        self.tasks: dict[str, dict[str, dict[str, Any]]] = {}
        self.provider_done: dict[str, float] = {}

    def now(self) -> float:
        return time.monotonic() - self.t0

    @property
    def running(self) -> bool:
        return self.ended is None

    def handle(self, event: RunEvent) -> None:
        with self.lock:
            at = self.now()
            if event.kind == "started":
                self.started[event.step] = at
            elif event.kind == "finished":
                self.finished[event.step] = at
            elif event.kind in ("stopped", "paused"):
                self.stopped = (event.step, at)
            elif event.kind == "task_done":
                d = event.detail
                self.tasks.setdefault(d["provider"], {})[d["doc_id"]] = {
                    "status": d["status"],
                    "at": at - self.started.get("trial_runner", at),
                    "cost_usd": d.get("cost_usd"),
                }
            elif event.kind == "provider_done":
                p = event.detail["provider"]
                self.provider_done[p] = at - self.started.get("trial_runner", at)

    def end(self, error: str | None = None) -> None:
        with self.lock:
            self.error = error
            self.ended = self.now()

    def wrap(self, steps: Steps) -> Steps:
        """`steps` with taps that record the registry, the candidates kept and the answer keys
        (none of which the store keeps)."""
        inner_registry, inner_candidates, inner_eval = steps.registry, steps.candidates, steps.eval

        async def registry() -> list[RegisteredProvider]:
            providers = inner_registry()
            if inspect.isawaitable(providers):
                providers = await providers
            with self.lock:
                self.registry = list(providers)
            return list(providers)

        def candidates(
            spec: JobSpec, registry: list[RegisteredProvider]
        ) -> list[RegisteredProvider]:
            kept = inner_candidates(spec, registry)
            with self.lock:
                self.kept = list(kept)
            return kept

        def build_eval(rubric: Rubric, trial: TrialPackage) -> ScoringConfig:
            config = inner_eval(rubric, trial)
            with self.lock:
                self.answer_keys = len(config.answer_keys)
            return config

        return dataclasses.replace(steps, registry=registry, candidates=candidates, eval=build_eval)

    # --- the page's view ----------------------------------------------------------------------

    def view(self, record: JobRecord, platform_usd: float) -> dict[str, Any]:
        with self.lock:
            now = self.ended if self.ended is not None else self.now()
            out: dict[str, Any] = {
                "job_id": self.job_id,
                "example": self.example,
                "mock": self.mock,
                "scorer": self.scorer,
                "running": self.running,
                "status": str(record.status),
                "error": self.error,
                "elapsed_s": round(now, 2),
                "posting": posting_view(record.posting),
                "steps": self._steps(record, now),
                "review": _review(record),
                "rubric": _rubric(record),
                "trial": _trial(record),
                "eval": None if self.answer_keys is None else {"answer_keys": self.answer_keys},
                "candidates": self._candidates(record),
                "lanes": self._lanes(record),
                "scores": _scores(record),
                "verdict": None
                if record.verdict is None
                else record.verdict.model_dump(mode="json"),
                "costs": self._costs(record, platform_usd),
            }
            out["outcome"] = _outcome(record, self.running, self.error)
            return out

    def _steps(self, record: JobRecord, now: float) -> list[dict[str, Any]]:
        failed = None
        if record.status in (JobStatus.REJECTED, JobStatus.NEEDS_INFO):
            failed = "review"
        elif self.stopped is not None:
            failed = self.stopped[0]
        out, after_failure = [], False
        for name in STEP_NAMES:
            started, finished = self.started.get(name), self.finished.get(name)
            if after_failure:
                state = "skipped"
            elif name == failed:
                state, after_failure = "failed", True
            elif finished is not None:
                state = "done"
            elif started is not None:
                state = "running" if self.running else "skipped"
            else:
                state = "skipped" if not self.running else "pending"
            end = finished
            if end is None and self.stopped is not None and self.stopped[0] == name:
                end = self.stopped[1]
            duration = None if started is None else (end if end is not None else now) - started
            out.append(
                {
                    "key": name,
                    "state": state,
                    "started_s": None if started is None else round(started, 2),
                    "duration_s": None if duration is None else round(duration, 2),
                }
            )
        return out

    def _candidates(self, record: JobRecord) -> dict[str, Any] | None:
        if self.registry is None:
            return None
        kept = None if self.kept is None else {p.provider_id for p in self.kept}
        found = {p.card.slug for p in self.registry}
        rows = []
        for p in self.registry:
            reason = None
            if kept is not None and p.provider_id not in kept and record.spec is not None:
                reason = rejection_reason(record.spec, p)
            rows.append(
                _provider(p)
                | {"qualifies": None if kept is None else p.provider_id in kept, "reason": reason}
            )
        return {
            "providers": rows,
            "unavailable": [s for s in self.slugs if s not in found],
            "done": kept is not None,
        }

    def _lanes(self, record: JobRecord) -> dict[str, Any] | None:
        if self.kept is None or record.trial is None:
            return None
        docs = [d.doc_id for d in record.trial.documents]
        lanes = []
        for p in self.kept:
            tasks = self.tasks.get(p.provider_id, {})
            counts: dict[str, int] = {}
            for t in tasks.values():
                counts[t["status"]] = counts.get(t["status"], 0) + 1
            lanes.append(
                _provider(p)
                | {
                    "cells": [
                        None
                        if d not in tasks
                        else {"status": tasks[d]["status"], "at_s": round(tasks[d]["at"], 2)}
                        for d in docs
                    ],
                    "counts": counts,
                    "finished_s": None
                    if p.provider_id not in self.provider_done
                    else round(self.provider_done[p.provider_id], 2),
                }
            )
        return {"docs": docs, "providers": lanes}

    def _costs(self, record: JobRecord, platform_usd: float) -> dict[str, float]:
        # D19, as run_job's summary: the providers' reported cost_usd, from the saved submissions
        # once the Trial Runner is done, from its events until then.
        if record.submissions:
            providers = sum(s.cost_usd or 0.0 for s in record.submissions)
        else:
            providers = sum(
                t["cost_usd"] or 0.0 for tasks in self.tasks.values() for t in tasks.values()
            )
        return {
            "platform_usd": platform_usd,
            "providers_usd": providers,
            "total_usd": platform_usd + providers,
        }


def _provider(p: RegisteredProvider) -> dict[str, Any]:
    c = p.card
    return {
        "id": p.provider_id,
        "slug": c.slug,
        "version": c.version,
        "price_per_doc_usd": c.price_per_doc_usd,
        "region": c.region,
        "pitch": c.description,
    }


def _review(record: JobRecord) -> dict[str, Any] | None:
    s = record.spec
    if s is None:
        return None
    return {
        "decision": s.decision,
        "reason": s.reason,
        "questions": s.questions,
        "job_type": s.job_type,
        "fields": s.fields,
        "per_doc_budget_usd": s.per_doc_budget_usd,
    }


def _rubric(record: JobRecord) -> dict[str, Any] | None:
    r = record.rubric
    if r is None:
        return None
    return {
        "weights": sorted(r.weights.items(), key=lambda kv: -kv[1]),
        "required_fields": r.required_fields,
        "consistency_checks": r.consistency_checks,
        "tolerances": r.tolerances.model_dump(),
    }


def _trial(record: JobRecord) -> dict[str, Any] | None:
    if record.trial is None:
        return None
    return {
        "docs": [
            {
                "doc_id": d.doc_id,
                "source": d.source,
                "vendor": d.answer.vendor_name,
                "date": d.answer.invoice_date,
                "items": len(d.answer.line_items),
                "total": d.answer.total,
                "currency": d.answer.currency,
            }
            for d in record.trial.documents
        ]
    }


def _scores(record: JobRecord) -> list[dict[str, Any]] | None:
    if not record.scores:
        return None
    return [
        {
            "provider": r.provider,
            "slug": r.provider.split("@")[0],
            "trial_score": r.trial_score,
            "ci_95": list(r.ci_95),
            "docs": [
                {"doc_id": d.doc_id, "score": d.score, "status": d.status} for d in r.doc_scores
            ],
        }
        for r in sorted(record.scores, key=lambda r: r.provider)
    ]


def _outcome(record: JobRecord, running: bool, error: str | None) -> str | None:
    """None while running, else ranked | rejected | needs_info | paused | error."""
    if running:
        return None
    if error is not None:
        return "error"
    if record.status in (JobStatus.RANKED, JobStatus.REJECTED, JobStatus.NEEDS_INFO):
        return str(record.status)
    if record.status == JobStatus.PAUSED:
        return "paused"
    return "error"


# --- the app ----------------------------------------------------------------------------------


class RunRequest(BaseModel):
    example: str
    mock: bool = False
    scorer: Literal["mock", "real"] = "real"


# (store, gateway, job_id) → the run's steps, built after the run's environment is set.
BuildSteps = Callable[[Store, Any, str], Steps]


def default_build_steps(store: Store, gateway: Any, job_id: str) -> Steps:
    """The real steps, with every model call tagged with the job ID (for `platform_spend`)."""
    from core.agents.job_reviewer import default_gateway
    from core.pipeline.config import build_steps
    from core.run_job import _JobGateway

    tagged = _JobGateway(gateway or default_gateway(), job_id)
    return build_steps(store, gateway=cast(Any, tagged))


def create_app(
    store: Store | None = None,
    *,
    build_steps: BuildSteps = default_build_steps,
    gateway: Any = None,
    serve_docs: bool = True,
) -> FastAPI:
    """`build_steps` and `gateway` let tests swap in stubs and a `FakeGateway`; `serve_docs=False`
    skips starting the doc server."""
    store = store or JsonStore()
    env0 = {k: os.environ.get(k) for k in ENV_KEYS}
    examples = {e.key: e for e in EXAMPLES}
    runs: dict[str, LiveRun] = {}
    start_lock = threading.Lock()
    docs_started = False
    app = FastAPI(title="Agent-Hunter demo")

    def configure(mock: bool, scorer: str) -> None:
        from core.run_job import use_mock_providers

        for k, v in env0.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        os.environ["SAMPLES_DIR"] = str(EXAMPLES_DIR.resolve())
        if mock:
            use_mock_providers()
        os.environ["SCORER"] = scorer

    def ensure_docserver() -> None:
        nonlocal docs_started
        if docs_started or not serve_docs:
            return
        from core.docserver import serve_in_thread
        from core.run_job import _port

        url = os.environ.get("DOCS_BASE_URL") or "http://localhost:8200"
        try:
            serve_in_thread(port=_port(url, 8200))
        except RuntimeError:
            log.info("doc server already running at %s", url)
        docs_started = True

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(INDEX_HTML, media_type="text/html")

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return {
            "examples": [
                {"key": e.key, "title": e.title, "short": e.short} | posting_view(load_posting(e))
                for e in EXAMPLES
            ],
            "defaults": {"mock": False, "scorer": (env0["SCORER"] or "mock").strip().lower()},
        }

    @app.post("/api/runs", status_code=202)
    def start_run(req: RunRequest) -> dict[str, str]:
        example = examples.get(req.example)
        if example is None:
            raise HTTPException(404, f"no example {req.example!r}")
        with start_lock:
            if any(r.running for r in runs.values()):
                raise HTTPException(409, "a run is already in progress")
            configure(req.mock, req.scorer)
            ensure_docserver()
            posting = load_posting(example)
            job_id = store.create_job(posting)
            live = LiveRun(job_id, example.key, req.mock, req.scorer)
            live.slugs = provider_slugs()
            steps = live.wrap(build_steps(store, gateway, job_id))
            runs[job_id] = live

        def work() -> None:
            error = None
            try:
                asyncio.run(
                    run_pipeline(
                        posting, store=store, steps=steps, on_event=live.handle, job_id=job_id
                    )
                )
            except Exception as exc:  # run_pipeline logged it and emitted `stopped`
                error = f"{type(exc).__name__}: {exc}"
            live.end(error)

        threading.Thread(target=work, name=f"run-{job_id}", daemon=True).start()
        return {"job_id": job_id}

    @app.get("/api/runs/{job_id}")
    def get_run(job_id: str) -> dict[str, Any]:
        live = runs.get(job_id)
        if live is None:
            raise HTTPException(404, f"no run {job_id}")
        try:
            record = store.get_job(job_id)
        except JobNotFound:
            raise HTTPException(404, f"no job {job_id}") from None
        return live.view(record, store.platform_spend(job_id))

    return app


def main(argv: list[str] | None = None) -> None:
    import argparse

    import uvicorn
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(prog="python -m core.web", description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8300)
    args = parser.parse_args(argv)
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print(f"Trial-then-Hire demo: http://localhost:{args.port}")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
