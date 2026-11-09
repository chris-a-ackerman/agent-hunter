"""The A2A front door (AGE-44): Agent-Hunter as an A2A agent that other agents can hire through.

    uv run python -m core.a2a [--host 127.0.0.1] [--port 8940]
    uv run uvicorn core.a2a:app --port 8940

    GET  /.well-known/agent-card.json   the agent card; the same bytes on every fetch, so Nanda
                                        Town can pin its digest
    POST /                              JSON-RPC 2.0. `message/send` runs one job through the
                                        whole pipeline and returns a task; `tasks/get` returns
                                        a stored task

The request's single text part is `{"job": <JobPosting>, "request_id": ..., "nonce": ...}` (Town
adds `request_id` and `nonce`). The reply is a `completed` task with exactly one text artifact: the
hire result of HW8 wire contracts §2, serialized once with sorted keys. Rejected, needs_info and
paused jobs are `completed` too, with `verdict: null` and the reason. A request that isn't a hire
request gets a `failed` task with `{"error": "invalid_request"}`, never `rejected` (Town's
`a2a test` probe sends a quote body and treats `rejected` as a problem); a pipeline crash gets
`failed` with the error class only. Only envelope problems get a JSON-RPC error.

Offline mode (`--offline` or AGENT_HUNTER_OFFLINE=1, AGE-45) needs no `.env` and no Parley key:
the stub Job Reviewer, Rubric Builder, Trial Builder and Ranker, the real Eval Builder, Candidate
Finder, Trial Runner and Scorer, and the mock providers (started on MOCK_PROVIDERS_BASE_URL if
nothing answers). Every model call raises, the card and every reply say `"mode": "offline"`, and
jobs go to their own store (STORE_DIR, default `.data/offline-store`).

Hire requests are idempotent on `request_id` (AGE-48, wire contracts §2). Terms are the canonical
JSON of the whole body minus `request_id` (so the nonce counts). The first request runs the
pipeline; an identical retry gets the stored reply back verbatim (same task, same job, same text
part, so Town's content digest matches) with no new job and no new spend; the same `request_id`
with different terms gets a `rejected` task. A retry that arrives mid-run waits for that run (one
lock per `request_id`). The mapping lives in the job store, so it survives a restart; a request
left `running` by a door that died mid-run is answered `failed` with `{"error": "interrupted"}`
(or with its result, if the job had already finished). A body without a string `request_id` isn't
deduped. `POST /jobs` (core.web) is unchanged.

Like `core.web`, the pipeline runs in-process and the doc server is started on DOCS_BASE_URL if
nothing answers there. The answer key never leaves the store: replies carry the
verdict (provider IDs and scores), never trial documents or their answers.
"""

import asyncio
import hashlib
import json
import logging
import os
import subprocess
import tomllib
import uuid
import weakref
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from contracts import JobPosting, JobStatus
from core.pipeline.runner import Steps, run_pipeline
from core.pipeline.stubs import NoModelGateway, forbid_model_calls, offline_steps
from core.store import A2ARequest, JobNotFound, JobRecord, JsonStore, Store
from core.web import BuildSteps, default_build_steps
from examples.make_examples import EXAMPLES_DIR

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8940
DEFAULT_PUBLIC_URL = f"http://127.0.0.1:{DEFAULT_PORT}"
OFFLINE_STORE_DIR = ".data/offline-store"
Mode = Literal["live", "offline"]


def offline_requested() -> bool:
    return os.environ.get("AGENT_HUNTER_OFFLINE", "").strip().lower() in ("1", "true", "yes")


def _offline_paths() -> tuple[Path, Path, Path]:
    """(samples, docs, truth). The mocks read the same truth dir the stub Trial Builder writes."""
    return (
        Path(os.environ.get("SAMPLES_DIR") or EXAMPLES_DIR),
        Path(os.environ.get("DOCS_DIR") or "data/docs"),
        Path(os.environ.get("TRUTH_DIR") or "data/truth"),
    )


def _mock_providers_url() -> str:
    return os.environ.get("MOCK_PROVIDERS_BASE_URL") or "http://localhost:8101"


def offline_build_steps(store: Store, gateway: Any, job_id: str) -> Steps:
    """The offline steps (`core.pipeline.stubs.offline_steps`); none takes a gateway."""
    from core.run_job import MOCK_DEADLINE_MS

    samples, docs, truth = _offline_paths()
    return offline_steps(
        store,
        samples_dir=samples,
        docs_dir=docs,
        truth_dir=truth,
        docs_base_url=os.environ.get("DOCS_BASE_URL") or "http://localhost:8200",
        providers_base_url=_mock_providers_url(),
        deadline_ms=int(os.environ.get("MOCK_TASK_DEADLINE_MS") or MOCK_DEADLINE_MS),
    )


def _version() -> str:
    """`<project version>+<git short SHA>`, or `+unknown` without git. Computed once."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        sha = ""
    return f"{project}+{sha or 'unknown'}"


VERSION = _version()


def agent_card(public_url: str, mode: Mode = "live") -> dict[str, Any]:
    """The A2A card: the keys Town's `build_agent_card` uses, plus our `mode`."""
    description = (
        "Trial-then-Hire for invoice extraction: post a job, and Agent-Hunter tests candidate "
        "provider agents on it and recommends one, with confidence intervals."
    )
    return {
        "name": "agent-hunter",
        "description": description,
        "url": public_url.rstrip("/") + "/",
        "version": VERSION,
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "skills": [
            {
                "id": "trial_then_hire",
                "name": "Trial then hire",
                "description": description,
                "tags": ["trial_then_hire", "invoice_extraction"],
            }
        ],
        "mode": mode,
    }


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _new_task_id() -> str:
    return f"task-{uuid.uuid4().hex[:12]}"


def _new_task(state: str, task_id: str | None = None) -> dict[str, Any]:
    return {
        "kind": "task",
        "id": task_id or _new_task_id(),
        "contextId": f"ctx-{uuid.uuid4().hex[:12]}",
        "status": {"state": state},
        "artifacts": [],
    }


def _status_task(state: str, error: str, detail: str, task_id: str | None = None) -> dict[str, Any]:
    task = _new_task(state, task_id)
    task["status"]["message"] = {
        "kind": "message",
        "role": "agent",
        "messageId": f"msg-{uuid.uuid4().hex[:12]}",
        "parts": [{"kind": "text", "text": _canonical({"error": error, "detail": detail})}],
    }
    return task


def _failed_task(error: str, detail: str, task_id: str | None = None) -> dict[str, Any]:
    return _status_task("failed", error, detail, task_id)


def _conflict_task() -> dict[str, Any]:
    """Same `request_id`, different terms (Town's `conflicting_retry`): the only `rejected`."""
    return _status_task(
        "rejected", "conflicting_request", "request_id already used with different terms"
    )


def _completed_task(result: dict[str, Any], task_id: str) -> dict[str, Any]:
    task = _new_task("completed", task_id)
    task["artifacts"] = [
        {
            "artifactId": "hire-result",
            "name": "hire_result",
            "parts": [{"kind": "text", "text": _canonical(result)}],
        }
    ]
    return task


def terms_digest(body: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON of the request minus `request_id` (Town's rule)."""
    terms = {k: v for k, v in body.items() if k != "request_id"}
    return hashlib.sha256(_canonical(terms).encode("utf-8")).hexdigest()


# The statuses `run_pipeline` returns in: a job left in one of them has its result.
_PIPELINE_DONE = frozenset(
    {JobStatus.RANKED, JobStatus.REJECTED, JobStatus.NEEDS_INFO, JobStatus.PAUSED}
)


def _rpc_result(rpc_id: Any, task: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": task})


def _rpc_error(rpc_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}
    )


def _message_text(params: Any) -> str | None:
    """The first text part of a message/send request, or None if the envelope has none."""
    message = params.get("message") if isinstance(params, dict) else None
    parts = message.get("parts") if isinstance(message, dict) else None
    if not isinstance(parts, list):
        return None
    for part in parts:
        if (
            isinstance(part, dict)
            and part.get("kind") == "text"
            and isinstance(part.get("text"), str)
        ):
            return part["text"]
    return None


def _parse_hire_request(text: str) -> tuple[dict[str, Any], JobPosting] | str:
    """(the request body, its posting), or why it isn't a hire request."""
    try:
        body = json.loads(text)
    except ValueError:
        return "the text part is not JSON"
    if not isinstance(body, dict) or "job" not in body:
        return 'expected a JSON object with a "job" posting'
    try:
        posting = JobPosting.model_validate(body["job"])
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "job"
        return f'"job" is not a valid JobPosting ({exc.error_count()} errors; first at {where})'
    return body, posting


def hire_result(
    record: JobRecord, request_id: Any, mode: Mode, platform_usd: float
) -> dict[str, Any]:
    """The hire result (wire contracts §2) for a job the pipeline finished."""
    ranked = record.status == JobStatus.RANKED
    note = record.status_history[-1].note if record.status_history else None
    providers_usd = sum(s.cost_usd or 0.0 for s in record.submissions)
    return {
        "request_id": request_id,
        "mode": mode,
        "job_id": record.job_id,
        "job_status": str(record.status),
        "reason": None if ranked else note,
        "verdict": record.verdict.model_dump(mode="json") if ranked and record.verdict else None,
        "spend_usd": {
            "platform_usd": platform_usd,
            "providers_usd": providers_usd,
            "total_usd": platform_usd + providers_usd,
        },
    }


class TaskStore:
    """A2A tasks as JSON files under `<store root>/a2a_tasks`, so `tasks/get` survives a restart."""

    def __init__(self, root: Path) -> None:
        self.dir = root / "a2a_tasks"

    def _path(self, task_id: str) -> Path | None:
        # Task IDs are ours (task-<hex>); anything else can't name a stored file.
        if not task_id.startswith("task-") or not task_id[5:].isalnum():
            return None
        return self.dir / f"{task_id}.json"

    def save(self, task: dict[str, Any]) -> None:
        path = self._path(task["id"])
        assert path is not None
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(task), "utf-8")
        tmp.replace(path)

    def get(self, task_id: str) -> dict[str, Any] | None:
        path = self._path(task_id)
        if path is None or not path.exists():
            return None
        return json.loads(path.read_text("utf-8"))


def create_app(
    store: Store | None = None,
    *,
    build_steps: BuildSteps = default_build_steps,
    gateway: Any = None,
    serve_docs: bool = True,
    mode: Mode = "live",
    public_url: str | None = None,
    tasks_dir: Path | None = None,
) -> FastAPI:
    """`build_steps` and `gateway` let tests swap in stubs and a `FakeGateway`; `serve_docs=False`
    skips starting the doc server (and, offline, the mock providers). `tasks_dir` defaults to the
    JSON store's root. `mode="offline"` uses `offline_build_steps` unless `build_steps` is given."""
    offline = mode == "offline"
    if offline:
        if build_steps is default_build_steps:
            build_steps = offline_build_steps
        gateway = NoModelGateway()
    store = store or JsonStore(
        os.environ.get("STORE_DIR") or (OFFLINE_STORE_DIR if offline else None)
    )
    tasks = TaskStore(tasks_dir or Path(getattr(store, "root", JsonStore().root)))
    card = agent_card(public_url or os.environ.get("A2A_PUBLIC_URL") or DEFAULT_PUBLIC_URL, mode)
    # Posting sample paths are relative to SAMPLES_DIR; default to the bundled examples, which
    # the Town profile's posting refers to.
    os.environ.setdefault("SAMPLES_DIR", str(EXAMPLES_DIR.resolve()))
    docs_started = False
    app = FastAPI(title="Agent-Hunter A2A")

    def ensure_servers() -> None:
        nonlocal docs_started
        if docs_started or not serve_docs:
            return
        from core.docserver import serve_app_in_thread, serve_in_thread
        from core.run_job import _port

        url = os.environ.get("DOCS_BASE_URL") or "http://localhost:8200"
        try:
            serve_in_thread(port=_port(url, 8200))
        except RuntimeError:
            log.info("doc server already running at %s", url)
        if offline:
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

    # One lock per request_id, so a retry that arrives mid-run waits for that run. Weak values:
    # a lock goes away once no request holds or waits on it. One event loop per app (uvicorn).
    locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def result_task(job_id: str, request_id: Any, task_id: str) -> dict[str, Any]:
        result = hire_result(store.get_job(job_id), request_id, mode, store.platform_spend(job_id))
        return _completed_task(result, task_id)

    async def hire(
        body: dict[str, Any], posting: JobPosting, pending: A2ARequest | None = None
    ) -> dict[str, Any]:
        """Run one job. With `pending` (a dedupe record), save it `running` before the pipeline
        starts and `done` with the reply once it's final."""
        ensure_servers()
        task_id = pending.task_id if pending else _new_task_id()
        job_id = store.create_job(posting)
        if pending:
            pending = pending.model_copy(update={"job_id": job_id})
            store.save_a2a_request(pending)
        steps = build_steps(store, gateway, job_id)
        try:
            await run_pipeline(posting, store=store, steps=steps, job_id=job_id)
        except Exception as exc:  # run_pipeline logged it; never send a traceback or env values
            detail = f"job {job_id} stopped: {type(exc).__name__}"
            task = _failed_task("pipeline_error", detail, task_id)
        else:
            task = result_task(job_id, body.get("request_id"), task_id)
        if pending:
            store.save_a2a_request(pending.model_copy(update={"state": "done", "task": task}))
        return task

    def recover(seen: A2ARequest) -> dict[str, Any]:
        """A `running` record found while holding its lock was left by a door that stopped
        mid-run. Never start a second job for it: reply with the job's result if it finished,
        else `interrupted`. Saved `done`, so later retries get the same reply."""
        try:
            record = store.get_job(seen.job_id) if seen.job_id else None
        except JobNotFound:
            record = None
        if (
            record is not None
            and record.status in _PIPELINE_DONE
            and (record.status != JobStatus.RANKED or record.verdict)
        ):
            task = result_task(record.job_id, seen.request_id, seen.task_id)
        else:
            detail = f"job {seen.job_id or '(none)'} was interrupted by a restart"
            task = _failed_task("interrupted", detail, seen.task_id)
        store.save_a2a_request(seen.model_copy(update={"state": "done", "task": task}))
        return task

    async def hire_once(body: dict[str, Any], posting: JobPosting) -> dict[str, Any]:
        """`hire`, deduped on `request_id` (AGE-48)."""
        request_id = body.get("request_id")
        if not isinstance(request_id, str):
            return await hire(body, posting)
        terms = terms_digest(body)
        lock = locks.get(request_id)
        if lock is None:
            lock = locks[request_id] = asyncio.Lock()
        async with lock:
            seen = store.get_a2a_request(request_id)
            if seen is None:
                pending = A2ARequest(
                    request_id=request_id,
                    terms_digest=terms,
                    state="running",
                    task_id=_new_task_id(),
                )
                return await hire(body, posting, pending)
            if seen.terms_digest != terms:
                return _conflict_task()
            if seen.state == "done" and seen.task is not None:
                return seen.task
            return recover(seen)

    @app.get("/.well-known/agent-card.json")
    def get_card() -> dict[str, Any]:
        return card

    @app.post("/")
    async def rpc(request: Request) -> JSONResponse:
        try:
            envelope = json.loads(await request.body())
        except (ValueError, UnicodeDecodeError):
            return _rpc_error(None, -32700, "parse error")
        if not isinstance(envelope, dict) or envelope.get("jsonrpc") != "2.0":
            return _rpc_error(None, -32600, "invalid request")
        rpc_id, params = envelope.get("id"), envelope.get("params")

        if envelope.get("method") == "tasks/get":
            task_id = params.get("id") if isinstance(params, dict) else None
            stored = tasks.get(task_id) if isinstance(task_id, str) else None
            if stored is None:
                return _rpc_error(rpc_id, -32001, "task not found")
            return _rpc_result(rpc_id, stored)
        if envelope.get("method") != "message/send":
            return _rpc_error(rpc_id, -32601, "method not found")

        text = _message_text(params)
        if text is None:
            return _rpc_error(rpc_id, -32602, "message/send needs a text part")
        parsed = _parse_hire_request(text)
        if isinstance(parsed, str):
            task = _failed_task("invalid_request", parsed)
        else:
            task = await hire_once(*parsed)
        tasks.save(task)
        return _rpc_result(rpc_id, task)

    return app


def __getattr__(name: str) -> Any:
    """`core.a2a:app` for uvicorn, built on first use so importing this module has no effects."""
    if name == "app":
        from dotenv import load_dotenv

        load_dotenv()
        mode: Mode = "offline" if offline_requested() else "live"
        if mode == "offline":
            forbid_model_calls()
        globals()["app"] = application = create_app(mode=mode)
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main(argv: list[str] | None = None) -> None:
    import argparse

    import uvicorn
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(prog="python -m core.a2a", description=__doc__.split("\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="no model calls and no Parley key: stub agents + mock providers (AGE-45)",
    )
    args = parser.parse_args(argv)
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    mode: Mode = "offline" if args.offline or offline_requested() else "live"
    if mode == "offline":
        forbid_model_calls()
    public_url = os.environ.get("A2A_PUBLIC_URL") or f"http://{args.host}:{args.port}"
    print(f"Agent-Hunter A2A door ({mode}): {public_url} (card at /.well-known/agent-card.json)")
    uvicorn.run(
        create_app(mode=mode, public_url=public_url),
        host=args.host,
        port=args.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
