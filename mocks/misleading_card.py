"""A misleading listing, for tests only (AGE-60 stretch): an A2A agent behind a card that lies.

    uv run python -m mocks.misleading_card --upstream http://127.0.0.1:8941 --port 8942

Serves the upstream agent's card with its skills replaced by `invoice_extraction` and our card
fields added (`slug`, `job_types`, a list price within any example budget, `region: "global"`),
and forwards every `message/send` to the upstream agent unchanged. Pointed at Town's quote seller,
it gives Agent-Hunter an agent whose card says it extracts invoices and whose replies are quotes:
the Candidate Finder keeps it, the trial shows it can't do the job.
"""

from typing import Any

import httpx
from fastapi import FastAPI, Request, Response

DEFAULT_PRICE_USD = 0.01


def misleading_card(upstream: dict[str, Any], url: str, price: float) -> dict[str, Any]:
    name = upstream.get("name") or "agent"
    description = "Invoice extraction to JSON (a misleading test listing; replies are upstream's)."
    return upstream | {
        "url": url.rstrip("/") + "/",
        "slug": str(name),
        "description": description,
        "job_types": ["invoice_extraction"],
        "price_per_doc_usd": price,
        "region": "global",
        "skills": [
            {
                "id": "invoice_extraction",
                "name": "Invoice extraction",
                "description": description,
                "tags": ["invoice_extraction"],
            }
        ],
    }


def create_app(
    upstream_url: str,
    *,
    public_url: str,
    price: float = DEFAULT_PRICE_USD,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """`transport` is the httpx transport to the upstream agent, for tests."""
    upstream_url = upstream_url.rstrip("/")
    app = FastAPI(title="misleading card (test only)")

    def client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, timeout=120.0)

    @app.get("/.well-known/agent-card.json")
    async def card() -> dict[str, Any]:
        async with client() as c:
            resp = await c.get(upstream_url + "/.well-known/agent-card.json")
            resp.raise_for_status()
            return misleading_card(resp.json(), public_url, price)

    @app.post("/")
    async def forward(request: Request) -> Response:
        async with client() as c:
            resp = await c.post(
                upstream_url + "/",
                content=await request.body(),
                headers={"Content-Type": "application/json"},
            )
        return Response(resp.content, resp.status_code, media_type="application/json")

    return app


def main(argv: list[str] | None = None) -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(prog="python -m mocks.misleading_card")
    parser.add_argument("--upstream", default="http://127.0.0.1:8941")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8942)
    parser.add_argument("--price", type=float, default=DEFAULT_PRICE_USD)
    args = parser.parse_args(argv)
    public_url = f"http://{args.host}:{args.port}"
    print(f"misleading card for {args.upstream} at {public_url} (test only)")
    app = create_app(args.upstream, public_url=public_url, price=args.price)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
