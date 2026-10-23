"""The document server (D17): `GET /d/{doc_id}.pdf` serves `DOCS_DIR/{doc_id}.pdf`, nothing else.

`doc_id` must be 12 lowercase hex characters, so no path (`../`, `data/truth/...`) can be
expressed; anything else is a 404. The router is mounted in the core API; `serve_in_thread`
runs it alone in a background uvicorn thread (for `run_job`, AGE-17).
"""

import os
import re
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse

DEFAULT_DOCS_DIR = "data/docs"
_DOC_ID = re.compile(r"^[0-9a-f]{12}$")


def docs_dir() -> Path:
    return Path(os.environ.get("DOCS_DIR", "").strip() or DEFAULT_DOCS_DIR)


def doc_router(root: Path | None = None) -> APIRouter:
    """`root` defaults to DOCS_DIR, read per request so tests and run_job can change it."""
    router = APIRouter()

    @router.get("/d/{doc_id}.pdf")
    def get_doc(doc_id: str) -> FileResponse:
        if not _DOC_ID.fullmatch(doc_id):
            raise HTTPException(404)
        path = (root or docs_dir()) / f"{doc_id}.pdf"
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="application/pdf")

    return router


def create_app(root: Path | None = None) -> FastAPI:
    app = FastAPI(title="Agent-Hunter documents")
    app.include_router(doc_router(root))
    return app


def serve_in_thread(
    port: int = 8200, host: str = "127.0.0.1", root: Path | None = None
) -> uvicorn.Server:
    """Start the doc server in a daemon thread and wait until it accepts requests.
    Stop it with `server.should_exit = True`."""
    server = uvicorn.Server(
        uvicorn.Config(create_app(root), host=host, port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, name="docserver", daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError(f"doc server failed to start on {host}:{port}")
        time.sleep(0.02)
    return server
