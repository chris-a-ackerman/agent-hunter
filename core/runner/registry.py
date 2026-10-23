"""The provider registry (AGE-14, D22): `PROVIDERS_BASE_URL` + `PROVIDER_SLUGS`.

Each provider is an opaque endpoint + agent card: the card is fetched from
`{base_url}/p/{slug}/.well-known/agent-card.json` and nothing else is assumed about it. The MVP
has no Supabase tables (D21), so the registry is the `Store`'s provider list.
"""

import asyncio
import logging

import httpx

from contracts import AgentCard, RegisteredProvider
from core.pipeline.config import provider_slugs, providers_base_url
from core.store import Store

log = logging.getLogger(__name__)


def card_url(base_url: str, slug: str) -> str:
    return f"{base_url.rstrip('/')}/p/{slug}/.well-known/agent-card.json"


async def _fetch(client: httpx.AsyncClient, base_url: str, slug: str) -> RegisteredProvider | None:
    url = card_url(base_url, slug)
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        card = AgentCard.model_validate_json(resp.content)
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("provider %s left out: card at %s unavailable (%s)", slug, url, exc)
        return None
    return RegisteredProvider(card=card, base_url=base_url)


async def refresh_registry(
    store: Store | None = None,
    *,
    base_url: str | None = None,
    slugs: list[str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout_s: float = 10.0,
) -> list[RegisteredProvider]:
    """Fetch every slug's card (default: `PROVIDERS_BASE_URL` and `PROVIDER_SLUGS`) at once.

    A provider whose card can't be fetched or parsed is logged and left out of this run; the rest
    are returned in slug order (and upserted into `store`, if given)."""
    base_url = base_url or providers_base_url()
    slugs = provider_slugs() if slugs is None else slugs
    async with httpx.AsyncClient(transport=transport, timeout=timeout_s) as client:
        fetched = await asyncio.gather(*(_fetch(client, base_url, s) for s in slugs))
    providers = sorted((p for p in fetched if p is not None), key=lambda p: p.card.slug)
    if store is not None:
        for p in providers:
            store.upsert_provider(p)
    return providers
