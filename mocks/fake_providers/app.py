"""A local HTTP server speaking the provider contract (plan §9.2) for the mocks in MOCKS."""

import asyncio
import os
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from contracts import AgentCard, Invoice, ProviderError, ProviderResult, ProviderTask

from .corruption import MOCKS, mock_output

VERSION = "1"
_DOC_URL = re.compile(r"/d/(?P<doc_id>[0-9a-f]{12})\.pdf$")


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


def create_app(
    *,
    slugs: list[str] | None = None,
    truth_dir: Path | None = None,
    fail_budget: bool = False,
) -> FastAPI:
    """slugs defaults to every mock; truth_dir to $TRUTH_DIR or data/truth.
    fail_budget makes every task return the D18 402 budget_exhausted body."""
    slugs = list(MOCKS) if slugs is None else slugs
    unknown = set(slugs) - set(MOCKS)
    if unknown:
        raise ValueError(f"unknown mock slugs: {sorted(unknown)}")
    truth_dir = Path(truth_dir or os.environ.get("TRUTH_DIR", "data/truth"))
    app = FastAPI(title="Agent-Hunter mock providers")

    @app.get("/p/{slug}/.well-known/agent-card.json")
    def get_card(slug: str):
        if slug not in slugs:
            return _error(404, "unknown_provider")
        return card(slug).model_dump()

    @app.post("/p/{slug}/tasks")
    async def post_task(slug: str, task: ProviderTask):
        if slug not in slugs:
            return _error(404, "unknown_provider")
        if fail_budget:
            return _error(402, "budget_exhausted")
        spec = MOCKS[slug]
        if spec.behaviour == "timeout":
            await asyncio.sleep(2 * task.deadline_ms / 1000)
            return _error(504, "deadline_exceeded")

        m = _DOC_URL.search(task.document_url)
        if not m:
            return _error(400, "bad_document_url")
        doc_id = m["doc_id"]
        truth_path = truth_dir / f"{doc_id}.json"
        if not truth_path.exists():
            return _error(404, "unknown_document")
        truth = Invoice.model_validate_json(truth_path.read_text())

        return ProviderResult(
            task_id=task.task_id,
            output=mock_output(slug, doc_id, truth),
            cost_usd=spec.cost_usd or 0.0,
            provider_version=VERSION,
        ).model_dump()

    return app
