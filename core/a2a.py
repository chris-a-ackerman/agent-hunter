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

This version is a pass-through on purpose: every `message/send` starts a new job, like `POST /jobs`
(dedupe is AGE-48). Like `core.web`, the pipeline runs in-process and the doc server is started on
DOCS_BASE_URL if nothing answers there. The answer key never leaves the store: replies carry the
verdict (provider IDs and scores), never trial documents or their answers.
"""

import json
import logging
import os
import subprocess
import tomllib
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from contracts import JobPosting, JobStatus
from core.pipeline.runner import run_pipeline
from core.store import JobRecord, JsonStore, Store
from core.web import BuildSteps, default_build_steps
from examples.make_examples import EXAMPLES_DIR

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PORT = 8940
DEFAULT_PUBLIC_URL = f"http://127.0.0.1:{DEFAULT_PORT}"
Mode = Literal["live", "offline"]


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


def _new_task(state: str) -> dict[str, Any]:
    return {
        "kind": "task",
        "id": f"task-{uuid.uuid4().hex[:12]}",
        "contextId": f"ctx-{uuid.uuid4().hex[:12]}",
        "status": {"state": state},
        "artifacts": [],
    }


def _failed_task(error: str, detail: str) -> dict[str, Any]:
    task = _new_task("failed")
    task["status"]["message"] = {
        "kind": "message",
        "role": "agent",
        "messageId": f"msg-{uuid.uuid4().hex[:12]}",
        "parts": [{"kind": "text", "text": _canonical({"error": error, "detail": detail})}],
    }
    return task


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
    skips starting the doc server. `tasks_dir` defaults to the JSON store's root."""
    store = store or JsonStore()
    tasks = TaskStore(tasks_dir or Path(getattr(store, "root", JsonStore().root)))
    card = agent_card(public_url or os.environ.get("A2A_PUBLIC_URL") or DEFAULT_PUBLIC_URL, mode)
    # Posting sample paths are relative to SAMPLES_DIR; default to the bundled examples, which
    # the Town profile's posting refers to.
    os.environ.setdefault("SAMPLES_DIR", str(EXAMPLES_DIR.resolve()))
    docs_started = False
    app = FastAPI(title="Agent-Hunter A2A")

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

    async def hire(body: dict[str, Any], posting: JobPosting) -> dict[str, Any]:
        ensure_docserver()
        job_id = store.create_job(posting)
        steps = build_steps(store, gateway, job_id)
        try:
            await run_pipeline(posting, store=store, steps=steps, job_id=job_id)
        except Exception as exc:  # run_pipeline logged it; never send a traceback or env values
            return _failed_task("pipeline_error", f"job {job_id} stopped: {type(exc).__name__}")
        result = hire_result(
            store.get_job(job_id), body.get("request_id"), mode, store.platform_spend(job_id)
        )
        task = _new_task("completed")
        task["artifacts"] = [
            {
                "artifactId": "hire-result",
                "name": "hire_result",
                "parts": [{"kind": "text", "text": _canonical(result)}],
            }
        ]
        return task

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
            task = await hire(*parsed)
        tasks.save(task)
        return _rpc_result(rpc_id, task)

    return app


def __getattr__(name: str) -> Any:
    """`core.a2a:app` for uvicorn, built on first use so importing this module has no effects."""
    if name == "app":
        from dotenv import load_dotenv

        load_dotenv()
        globals()["app"] = application = create_app()
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main(argv: list[str] | None = None) -> None:
    import argparse

    import uvicorn
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(prog="python -m core.a2a", description=__doc__.split("\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    public_url = os.environ.get("A2A_PUBLIC_URL") or f"http://{args.host}:{args.port}"
    print(f"Agent-Hunter A2A door: {public_url} (card at /.well-known/agent-card.json)")
    uvicorn.run(
        create_app(public_url=public_url), host=args.host, port=args.port, log_level="warning"
    )


if __name__ == "__main__":
    main()
