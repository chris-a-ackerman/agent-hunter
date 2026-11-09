"""A local HTTP server speaking the provider contract (plan §9.2) for the mocks in MOCKS.

Every mock answers over plain HTTP (`POST /p/{slug}/tasks`) and over A2A (`POST /p/{slug}/a2a/`,
JSON-RPC `message/send`), with the same wire shape as the real providers (HW8 wire contracts §3).
"""

import asyncio
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from contracts import AgentCard, Invoice, ProviderError, ProviderResult, ProviderTask

from .corruption import MOCKS, mock_output

VERSION = "1"
_DOC_URL = re.compile(r"/d/(?P<doc_id>[0-9a-f]{12})\.pdf$")
# Answer keys for fixed documents that no trial wrote to TRUTH_DIR, e.g. the synthetic invoice
# pinned by Nanda Town's a2a-invoice-extraction profile (AGE-58).
FIXTURES_DIR = Path(__file__).parent / "fixtures"
DEFAULT_BASE_URL = "http://localhost:8101"


def _error(status: int, error: str) -> JSONResponse:
    return JSONResponse(ProviderError(error=error).model_dump(), status_code=status)


def card(slug: str) -> AgentCard:
    spec = MOCKS[slug]
    return AgentCard(
        slug=slug,
        name=f"Mock {slug.removeprefix('mock-').upper()}",
        description="Test double with a known, documented accuracy (mocks/README.md).",
        version=VERSION,
        job_types=["invoice_extraction"],
        price_per_doc_usd=spec.price_per_doc_usd,
        region="global",
    )


def a2a_card(slug: str, base_url: str) -> dict[str, Any]:
    """The standard A2A agent card, plus our AgentCard fields as extra keys."""
    c = card(slug)
    return {
        "name": c.name,
        "description": c.description,
        "url": f"{base_url}/p/{slug}/a2a/",
        "version": c.version,
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "skills": [
            {
                "id": "invoice_extraction",
                "name": "Invoice extraction",
                "description": c.description,
                "tags": ["invoice_extraction"],
            }
        ],
        "slug": c.slug,
        "job_types": c.job_types,
        "price_per_doc_usd": c.price_per_doc_usd,
        "region": c.region,
    }


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _task(state: str, *, text: str | None = None, error: dict[str, str] | None = None) -> dict:
    """An A2A task: `completed` with one text artifact, or another state with an error message."""
    task: dict[str, Any] = {
        "kind": "task",
        "id": f"task-{uuid.uuid4().hex[:12]}",
        "contextId": f"ctx-{uuid.uuid4().hex[:12]}",
        "status": {"state": state},
        "artifacts": [],
    }
    if text is not None:
        task["artifacts"] = [
            {"artifactId": "result", "name": "result", "parts": [{"kind": "text", "text": text}]}
        ]
    if error is not None:
        task["status"]["message"] = {
            "kind": "message",
            "role": "agent",
            "messageId": f"msg-{uuid.uuid4().hex[:12]}",
            "parts": [{"kind": "text", "text": _canonical(error)}],
        }
    return task


def _rpc_result(rpc_id: Any, task: dict) -> JSONResponse:
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


def create_app(
    *,
    slugs: list[str] | None = None,
    truth_dir: Path | None = None,
    fail_budget: bool = False,
    base_url: str | None = None,
) -> FastAPI:
    """slugs defaults to every mock; truth_dir to $TRUTH_DIR or data/truth.
    fail_budget makes every task return the D18 402 budget_exhausted body.
    base_url (default $MOCK_PROVIDERS_BASE_URL, else localhost:8101) fills the A2A cards' `url`."""
    slugs = list(MOCKS) if slugs is None else slugs
    unknown = set(slugs) - set(MOCKS)
    if unknown:
        raise ValueError(f"unknown mock slugs: {sorted(unknown)}")
    truth_dir = Path(truth_dir or os.environ.get("TRUTH_DIR", "data/truth"))
    base_url = (base_url or os.environ.get("MOCK_PROVIDERS_BASE_URL") or DEFAULT_BASE_URL).rstrip(
        "/"
    )
    app = FastAPI(title="Agent-Hunter mock providers")
    # (slug, request_id) → (terms, stored reply task). In memory, per process (wire contracts §3).
    a2a_replies: dict[tuple[str, str], tuple[str, dict]] = {}

    async def fulfil(slug: str, task: ProviderTask) -> ProviderResult | tuple[int, str]:
        """The mock's answer to one task, or (HTTP status, error code)."""
        if fail_budget:
            return 402, "budget_exhausted"
        spec = MOCKS[slug]
        if spec.behaviour == "timeout":
            await asyncio.sleep(2 * task.deadline_ms / 1000)
            return 504, "deadline_exceeded"

        m = _DOC_URL.search(task.document_url)
        if not m:
            return 400, "bad_document_url"
        doc_id = m["doc_id"]
        truth_path = truth_dir / f"{doc_id}.json"
        if not truth_path.exists():
            truth_path = FIXTURES_DIR / f"{doc_id}.json"
        if not truth_path.exists():
            return 404, "unknown_document"
        truth = Invoice.model_validate_json(truth_path.read_text())

        return ProviderResult(
            task_id=task.task_id,
            output=mock_output(slug, doc_id, truth),
            cost_usd=spec.cost_usd or 0.0,
            provider_version=VERSION,
        )

    @app.get("/p/{slug}/.well-known/agent-card.json")
    def get_card(slug: str):
        if slug not in slugs:
            return _error(404, "unknown_provider")
        return card(slug).model_dump()

    @app.post("/p/{slug}/tasks")
    async def post_task(slug: str, task: ProviderTask):
        if slug not in slugs:
            return _error(404, "unknown_provider")
        result = await fulfil(slug, task)
        if isinstance(result, tuple):
            return _error(*result)
        return result.model_dump()

    @app.get("/p/{slug}/a2a/.well-known/agent-card.json")
    def get_a2a_card(slug: str):
        if slug not in slugs:
            return _error(404, "unknown_provider")
        return a2a_card(slug, base_url)

    async def a2a(slug: str, request: Request):
        if slug not in slugs:
            return _error(404, "unknown_provider")
        try:
            envelope = json.loads(await request.body())
        except (ValueError, UnicodeDecodeError):
            return _rpc_error(None, -32700, "parse error")
        if not isinstance(envelope, dict) or envelope.get("jsonrpc") != "2.0":
            return _rpc_error(None, -32600, "invalid request")
        rpc_id = envelope.get("id")
        if envelope.get("method") != "message/send":
            return _rpc_error(rpc_id, -32601, "method not found")
        text = _message_text(envelope.get("params"))
        if text is None:
            return _rpc_error(rpc_id, -32602, "message/send needs a text part")

        # Not a ProviderTask (e.g. Town's quote probe) → failed, never rejected.
        try:
            body = json.loads(text)
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return _rpc_result(rpc_id, _task("failed", error={"error": "invalid_request"}))
        request_id = body.get("request_id")
        try:
            task = ProviderTask.model_validate(
                {k: v for k, v in body.items() if k not in ("request_id", "nonce")}
            )
        except ValidationError:
            return _rpc_result(rpc_id, _task("failed", error={"error": "invalid_request"}))

        key = (slug, request_id) if isinstance(request_id, str) else None
        terms = _canonical({k: v for k, v in body.items() if k != "request_id"})
        if key in a2a_replies:
            stored_terms, stored = a2a_replies[key]
            if stored_terms == terms:
                return _rpc_result(rpc_id, stored)  # an identical retry: same text, no new work
            return _rpc_result(
                rpc_id,
                _task(
                    "rejected",
                    error={
                        "error": "conflicting_terms",
                        "detail": "request_id already used with different terms",
                    },
                ),
            )

        result = await fulfil(slug, task)
        if isinstance(result, tuple):
            return _rpc_result(rpc_id, _task("failed", error={"error": result[1]}))
        reply = _task(
            "completed",
            text=_canonical({**result.model_dump(mode="json"), "request_id": request_id}),
        )
        if key is not None:
            a2a_replies[key] = (terms, reply)
        return _rpc_result(rpc_id, reply)

    # Real routes for both spellings: httpx doesn't follow FastAPI's slash redirect by default.
    app.add_api_route("/p/{slug}/a2a/", a2a, methods=["POST"])
    app.add_api_route("/p/{slug}/a2a", a2a, methods=["POST"])

    return app
