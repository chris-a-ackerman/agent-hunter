"""The core API (plan §9.0): `POST /jobs`, `GET /jobs/{id}`, plus the doc server (D17).

    uv run uvicorn core.api:app --port 8200

The MVP runs each job in-process as a background task.
"""

import logging
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException

from contracts import JobPosting
from core.docserver import doc_router
from core.pipeline.config import build_steps
from core.pipeline.runner import Steps, run_pipeline
from core.store import JobNotFound, JsonStore, Store

log = logging.getLogger(__name__)


def create_app(store: Store | None = None, steps: Steps | None = None) -> FastAPI:
    store = store or JsonStore()
    steps = steps or build_steps(store)
    app = FastAPI(title="Agent-Hunter core")
    app.include_router(doc_router())

    async def run(job_id: str, posting: JobPosting) -> None:
        try:
            await run_pipeline(posting, store=store, steps=steps, job_id=job_id)
        except Exception:
            pass  # run_pipeline already logged it and emitted `stopped`; the response is sent

    @app.post("/jobs", status_code=202)
    def post_job(posting: JobPosting, background: BackgroundTasks) -> dict[str, str]:
        job_id = store.create_job(posting)
        background.add_task(run, job_id, posting)
        return {"job_id": job_id}

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        try:
            record = store.get_job(job_id)
        except JobNotFound:
            raise HTTPException(404, f"no job {job_id}") from None
        # The trial carries the answer key, which never leaves the core: this app also serves
        # documents to providers, so it must not serve the answers too.
        return record.model_dump(mode="json", exclude={"trial"})

    return app


app = create_app()
