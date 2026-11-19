"""The demo web app (AGE-32): one page that runs an example job and shows each step live.

    uv run python -m core.web [--port 8300] [--offline]    then open http://localhost:8300
    uv run python -m core.web [--offline] --export-replay <job_id>

Serves `apps/web/index.html` and the small JSON API it polls:

    GET  /api/config         the example jobs, the rehearsal toggles' defaults, `mode` and
                             `tie_break`
    POST /api/runs           {"example": "inv-clean", "mock": false, "scorer": "real"} → job_id
    GET  /api/runs/{job_id}  everything the page shows: step states and timings, what each step
                             produced, the candidates, the provider lanes, scores, verdict, costs,
                             `mode` ("live" | "offline") and `tie_break` ("accuracy_first" | "cost",
                             from `core.eval.stats.accuracy_first()`)
    GET  /api/replays        one entry per recorded run: job_id, example, mode, created_at,
                             duration_s, outcome (AGE-68)
    GET  /api/replays/{job_id}  that run's timeline, oldest first (404 if there is none)
    GET  /replays/{file}.json   a committed replay from `apps/web/replays/` (`?replay=replays/…`)

Every run started from the page records a timeline (AGE-68): after each `RunEvent` and once at the
end, `{"t": elapsed_s, "view": <run view>}` is appended to `<STORE_DIR>/web_runs/<job_id>.jsonl`.
`--export-replay <job_id>` copies one to `apps/web/replays/<job_id>.json` (a JSON array), which the
page plays back with `?replay=<job_id>` or `?replay=replays/<job_id>.json`, sped up and labelled.

Offline mode (`--offline` or AGENT_HUNTER_OFFLINE=1, AGE-67) is the A2A door's offline wiring
(`core.a2a.offline_build_steps`): the stub Job Reviewer, Rubric Builder, Trial Builder and Ranker,
the real Eval Builder, Candidate Finder, Trial Runner and Scorer, and the mock providers (started on
MOCK_PROVIDERS_BASE_URL if nothing answers). No `.env`, no Parley key, every model call raises, and
jobs go to their own store (STORE_DIR, default `.data/offline-store`). The rehearsal toggles don't
apply offline. Agents listed in PROVIDER_A2A_URLS are fetched in both modes.

Like `run_job`, the pipeline runs in-process (one job at a time, in its own thread), the doc
server is started on DOCS_BASE_URL if nothing answers there, and "Mock providers" uses run_job's
`--mock` wiring. It's a separate app from `core.api` on purpose: the page shows the trial
documents' vendors and totals, which come from the answer key, and `core.api` (which providers
reach for documents) never serves the answer key.
"""

import asyncio
import dataclasses
import inspect
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
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
from core.eval.stats import accuracy_first
from core.pipeline.config import provider_a2a_urls, provider_slugs
from core.pipeline.runner import Steps, run_pipeline
from core.runner.candidate_finder import exclusions
from core.store import JobNotFound, JobRecord, JsonStore, Store
from core.store.json_store import DEFAULT_ROOT
from examples.make_examples import EXAMPLES_DIR

log = logging.getLogger(__name__)

INDEX_HTML = Path(__file__).resolve().parent.parent / "apps" / "web" / "index.html"
REPLAYS_DIR = INDEX_HTML.parent / "replays"  # committed replays (`--export-replay`, AGE-68)

Mode = Literal["live", "offline"]
TieBreak = Literal["accuracy_first", "cost"]

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


def tie_break() -> TieBreak:
    """The Ranker's tie-break rule right now (`core.eval.stats.accuracy_first`, AGE-66)."""
    return "accuracy_first" if accuracy_first() else "cost"


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

    def __init__(
        self, job_id: str, example: str, mock: bool, scorer: str, mode: Mode = "live"
    ) -> None:
        self.job_id, self.example, self.mock, self.scorer = job_id, example, mock, scorer
        self.mode: Mode = mode
        self.tie_break: TieBreak = tie_break()  # the rule this run's Ranker uses
        self.lock = threading.RLock()  # re-entrant: `handle` takes a timeline snapshot under it
        self.t0 = time.monotonic()
        self.ended: float | None = None
        self.started: dict[str, float] = {}
        self.finished: dict[str, float] = {}
        self.stopped: tuple[str, float] | None = None  # (step, at)
        self.error: str | None = None
        self.slugs: list[str] = []  # the configured registry, to name providers left out
        self.a2a_urls: list[str] = []  # PROVIDER_A2A_URLS, to tell each row's listing source
        self.registry: list[RegisteredProvider] | None = None
        self.unlisted: list[tuple[str, str]] = []  # (card URL, reason): `Registry.unlisted`
        self.kept: list[RegisteredProvider] | None = None
        self.answer_keys: int | None = None
        # provider → doc_id → {status, at (s since the trial step started), cost_usd}
        self.tasks: dict[str, dict[str, dict[str, Any]]] = {}
        self.provider_done: dict[str, float] = {}
        # The timeline (AGE-68): where snapshots go and how to build one; set by create_app.
        self.timeline: Path | None = None
        self.snapshot_view: Callable[[], dict[str, Any]] | None = None

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
            self.record_snapshot()

    def end(self, error: str | None = None) -> None:
        with self.lock:
            self.error = error
            self.ended = self.now()
            self.record_snapshot()

    def record_snapshot(self) -> None:
        """Append `{"t": elapsed_s, "view": <run view>}` to the run's timeline (AGE-68). Called in
        the pipeline thread, under the lock. A failed write logs a warning; the run goes on."""
        if self.timeline is None or self.snapshot_view is None:
            return
        try:
            with self.lock:
                t = self.ended if self.ended is not None else self.now()
                line = json.dumps({"t": round(t, 3), "view": self.snapshot_view()})
                self.timeline.parent.mkdir(parents=True, exist_ok=True)
                with self.timeline.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception as exc:
            name = type(exc).__name__
            log.warning("timeline write failed for %s (%s: %s)", self.job_id, name, exc)

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
                self.unlisted = list(getattr(providers, "unlisted", []))
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
                "mode": self.mode,
                "tie_break": self.tie_break,
                "started_at": record.created_at.isoformat(),  # the run's date, for replays
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

    def _source(self, url: str) -> str:
        """The listing source: `a2a` if `url` (a provider's base URL or a card URL) is one of
        PROVIDER_A2A_URLS, else `slugs` (listed by PROVIDERS_BASE_URL + PROVIDER_SLUGS)."""
        url = url.rstrip("/")
        for a2a in self.a2a_urls:
            a2a = a2a.rstrip("/")
            if url == a2a or url.startswith(a2a + "/.well-known/"):
                return "a2a"
        return "slugs"

    def _candidates(self, record: JobRecord) -> dict[str, Any] | None:
        """Every listed provider with its listing `source` and, once the Candidate Finder has
        run, whether it qualifies and why not (the same reasons as the A2A hire result's
        `excluded`, AGE-60), plus `unlisted`: each card that couldn't be listed at all."""
        if self.registry is None:
            return None
        kept = None if self.kept is None else {p.provider_id for p in self.kept}
        reasons: dict[str, str] = {}
        if self.kept is not None and record.spec is not None:
            excluded = exclusions(record.spec, self.registry, self.kept)
            reasons = {e["id"]: e["reason"] for e in excluded}
        found = {p.card.slug for p in self.registry}
        rows = [
            _provider(p)
            | {
                "source": self._source(p.base_url),
                "qualifies": None if kept is None else p.provider_id in kept,
                "reason": reasons.get(p.provider_id),
            }
            for p in self.registry
        ]
        return {
            "providers": rows,
            "unavailable": [s for s in self.slugs if s not in found],
            "unlisted": [
                {"id": where, "reason": why, "source": self._source(where)}
                for where, why in self.unlisted
            ],
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
        "transport": p.transport,
        "url": p.a2a_url or p.base_url,
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


# --- timelines and replays (AGE-68) -----------------------------------------------------------

_REPLAY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_REPLAY_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.json$")


def timeline_dir(store: Store | None = None, *, offline: bool = False) -> Path:
    """`<STORE_DIR>/web_runs`, with STORE_DIR resolved as `JsonStore` does (the store's own root
    if it has one; `.data/offline-store` offline, as `core.a2a`)."""
    from core.a2a import OFFLINE_STORE_DIR  # core.a2a imports this module

    root = getattr(store, "root", None) or os.environ.get("STORE_DIR")
    if not root:
        root = OFFLINE_STORE_DIR if offline else DEFAULT_ROOT
    return Path(root) / "web_runs"


def read_timeline(path: Path) -> list[dict[str, Any]]:
    """A timeline's snapshots, oldest first. A line that doesn't parse (a write cut short) is
    skipped; snapshots are otherwise returned exactly as recorded."""
    out = []
    for n, line in enumerate(path.read_text("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            snap = json.loads(line)
        except json.JSONDecodeError:
            log.warning("%s:%d: skipped a snapshot that isn't JSON", path, n)
            continue
        out.append(snap)
    return sorted(out, key=lambda s: s["t"])  # stable: already in order as written


def replay_summary(path: Path) -> dict[str, Any] | None:
    snaps = read_timeline(path)
    if not snaps:
        return None
    first, last = snaps[0]["view"], snaps[-1]["view"]
    created = first.get("started_at")
    if created is None:
        from datetime import UTC, datetime

        created = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    return {
        "job_id": path.stem,
        "example": last.get("example"),
        "mode": last.get("mode"),
        "created_at": created,
        "duration_s": snaps[-1]["t"],
        "outcome": last.get("outcome"),
    }


def export_replay(
    job_id: str,
    *,
    offline: bool = False,
    out_dir: Path | None = None,
    store: Store | None = None,
) -> Path:
    """Write `<out_dir>/<job_id>.json` (default `apps/web/replays/`): the run's timeline as a JSON
    array, one snapshot per line, unchanged. Looks in the store's `web_runs` and, with STORE_DIR
    unset, the other mode's default store too."""
    out_dir = out_dir or REPLAYS_DIR
    if not _REPLAY_ID.match(job_id):
        raise FileNotFoundError(f"no timeline for {job_id!r}")
    dirs = [timeline_dir(store, offline=offline)]
    if store is None and not os.environ.get("STORE_DIR"):
        dirs.append(timeline_dir(offline=not offline))
    for d in dirs:
        src = d / f"{job_id}.jsonl"
        if src.is_file():
            snaps = read_timeline(src)
            out_dir.mkdir(parents=True, exist_ok=True)
            dest = out_dir / f"{job_id}.json"
            dest.write_text("[\n" + ",\n".join(json.dumps(s) for s in snaps) + "\n]\n", "utf-8")
            return dest
    raise FileNotFoundError(f"no timeline for {job_id} in " + " or ".join(str(d) for d in dirs))


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
    offline: bool = False,
) -> FastAPI:
    """`build_steps` and `gateway` let tests swap in stubs and a `FakeGateway`; `serve_docs=False`
    skips starting the doc server (and, offline, the mock providers). `offline=True` uses
    `core.a2a.offline_build_steps` unless `build_steps` is given, a gateway that refuses every
    call, and the offline store unless `store` or STORE_DIR is given."""
    # Imported here: core.a2a imports this module.
    from core.a2a import OFFLINE_STORE_DIR, offline_build_steps
    from core.pipeline.stubs import NoModelGateway
    from mocks.fake_providers import MOCKS

    mode: Mode = "offline" if offline else "live"
    if offline:
        if build_steps is default_build_steps:
            build_steps = offline_build_steps
        gateway = NoModelGateway()
        store = store or JsonStore(os.environ.get("STORE_DIR") or OFFLINE_STORE_DIR)
    store = store or JsonStore()
    timelines = timeline_dir(store, offline=offline)  # AGE-68
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
        if offline:  # offline_build_steps wires the mocks and the real Scorer itself
            return
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
        if offline:
            # The mocks read the truth dir the stub Trial Builder writes (as in core.a2a).
            from core.a2a import _mock_providers_url, _offline_paths
            from core.docserver import serve_app_in_thread
            from mocks.fake_providers import create_app as create_mocks

            mocks_url = _mock_providers_url()
            try:
                serve_app_in_thread(
                    create_mocks(truth_dir=_offline_paths()[2], base_url=mocks_url),
                    port=_port(mocks_url, 8101),
                    name="mock providers",
                )
            except RuntimeError:
                log.info("mock providers already running at %s", mocks_url)
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
            "defaults": defaults(),
            "mode": mode,
            "tie_break": tie_break(),
        }

    def defaults() -> dict[str, Any]:
        """The rehearsal toggles' defaults; offline they are fixed (mocks, real Scorer)."""
        if offline:
            return {"mock": True, "scorer": "real"}
        return {"mock": False, "scorer": (env0["SCORER"] or "mock").strip().lower()}

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
            if offline:
                live = LiveRun(job_id, example.key, True, "real", mode)
                live.slugs = list(MOCKS)
            else:
                live = LiveRun(job_id, example.key, req.mock, req.scorer, mode)
                live.slugs = provider_slugs()
            live.a2a_urls = provider_a2a_urls()
            live.timeline = timelines / f"{job_id}.jsonl"
            live.snapshot_view = lambda: live.view(
                store.get_job(job_id), store.platform_spend(job_id)
            )
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

    # --- replays (AGE-68) -------------------------------------------------------------------

    @app.get("/api/replays")
    def list_replays() -> list[dict[str, Any]]:
        if not timelines.is_dir():
            return []
        out = []
        for path in timelines.glob("*.jsonl"):
            try:
                summary = replay_summary(path)
            except (OSError, KeyError, TypeError) as exc:
                log.warning("skipped timeline %s (%s)", path.name, exc)
                continue
            if summary is not None:
                out.append(summary)
        return sorted(out, key=lambda r: r["created_at"] or "", reverse=True)

    @app.get("/api/replays/{job_id}")
    def get_replay(job_id: str) -> JSONResponse:
        path = timelines / f"{job_id}.jsonl"
        if not _REPLAY_ID.match(job_id) or not path.is_file():
            raise HTTPException(404, f"no replay {job_id}")
        return JSONResponse(read_timeline(path))

    @app.get("/replays/{name}", include_in_schema=False)
    def committed_replay(name: str) -> FileResponse:
        path = REPLAYS_DIR / name
        if not _REPLAY_FILE.match(name) or ".." in name or not path.is_file():
            raise HTTPException(404, f"no replay file {name}")
        return FileResponse(path, media_type="application/json")

    return app


def main(argv: list[str] | None = None) -> None:
    import argparse

    import uvicorn
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(prog="python -m core.web", description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8300)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="no model calls and no Parley key: stub agents + mock providers (as core.a2a)",
    )
    parser.add_argument(
        "--export-replay",
        metavar="JOB_ID",
        help="write that run's timeline to apps/web/replays/<job_id>.json and exit (AGE-68)",
    )
    args = parser.parse_args(argv)
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from core.a2a import offline_requested
    from core.pipeline.stubs import forbid_model_calls

    offline = args.offline or offline_requested()
    if args.export_replay:
        try:
            dest = export_replay(args.export_replay, offline=offline)
        except FileNotFoundError as exc:
            raise SystemExit(str(exc)) from None
        print(f"wrote {dest}; play it with ?replay=replays/{dest.name}")
        return
    if offline:
        forbid_model_calls()
    label = " (offline · stub agents)" if offline else ""
    print(f"Trial-then-Hire demo{label}: http://localhost:{args.port}")
    uvicorn.run(create_app(offline=offline), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
