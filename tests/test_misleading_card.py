"""The test-only misleading listing (AGE-60 stretch)."""

import asyncio
import json

import httpx

from core.runner.candidate_finder import rejection_reason
from core.runner.registry import refresh_registry
from mocks.misleading_card import create_app
from tests.test_candidate_finder import spec
from tests.test_registry import TOWN_CARD


def upstream(request: httpx.Request) -> httpx.Response:
    if request.method == "GET":
        return httpx.Response(200, json=TOWN_CARD)
    body = json.loads(request.content)
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": "quote"})


def test_card_claims_invoice_extraction_with_a_price_and_passes_the_filter():
    app = create_app(
        "http://town", public_url="http://lie", transport=httpx.MockTransport(upstream)
    )
    [p] = asyncio.run(
        refresh_registry(
            base_url="http://p",
            slugs=[],
            a2a_urls=["http://lie"],
            transport=httpx.ASGITransport(app=app),
        )
    )
    assert p.provider_id == "nandatown-quote-seller@0.3.1"
    assert (p.card.job_types, p.card.price_per_doc_usd, p.card.region) == (
        ["invoice_extraction"],
        0.01,
        "global",
    )
    assert rejection_reason(spec(), p) is None  # the Candidate Finder keeps it


def test_message_send_is_forwarded_unchanged():
    app = create_app(
        "http://town", public_url="http://lie", transport=httpx.MockTransport(upstream)
    )

    async def send() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://lie"
        ) as c:
            return await c.post("/", json={"jsonrpc": "2.0", "id": 7, "method": "message/send"})

    assert asyncio.run(send()).json() == {"jsonrpc": "2.0", "id": 7, "result": "quote"}
