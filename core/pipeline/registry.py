"""The provider registry from config (D22): `PROVIDERS_BASE_URL` + `PROVIDER_SLUGS`.

Each provider is an opaque endpoint + agent card: the card is fetched from
`{base_url}/p/{slug}/.well-known/agent-card.json` and nothing else is assumed about it.
"""

import logging

import httpx

from contracts import AgentCard, RegisteredProvider
from core.store import Store

log = logging.getLogger(__name__)


def card_url(base_url: str, slug: str) -> str:
    return f"{base_url.rstrip('/')}/p/{slug}/.well-known/agent-card.json"


def load_registry(
    base_url: str,
    slugs: list[str],
    *,
    store: Store | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout_s: float = 10.0,
) -> list[RegisteredProvider]:
    """Fetch every slug's card. A provider whose card can't be fetched or parsed is logged and
    left out of this run; the rest are returned (and upserted into `store`, if given)."""
    providers = []
    with httpx.Client(transport=transport, timeout=timeout_s) as client:
        for slug in slugs:
            url = card_url(base_url, slug)
            try:
                resp = client.get(url)
                resp.raise_for_status()
                card = AgentCard.model_validate_json(resp.content)
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("provider %s left out: card at %s unavailable (%s)", slug, url, exc)
                continue
            p = RegisteredProvider(card=card, base_url=base_url)
            if store is not None:
                store.upsert_provider(p)
            providers.append(p)
    return providers
