"""The provider registry (AGE-14, D22, AGE-59): which provider agents a run can consider.

Two sources, both fetched once per run:

* `PROVIDERS_BASE_URL` + `PROVIDER_SLUGS`: our own layout. Each card is fetched from
  `{base_url}/p/{slug}/.well-known/agent-card.json` and must parse as our `AgentCard`. With
  `PROVIDER_TRANSPORT=a2a` the same cards are used, but tasks go to `{base_url}/p/{slug}/a2a`
  (HW8 wire contracts §3).
* `PROVIDER_A2A_URLS`: A2A agents anywhere, by base URL. The card comes from
  `{url}/.well-known/agent-card.json`, or `{url}/.well-known/agent.json` when that answers 404/410
  (Town's rule). A card with our `AgentCard` fields is used as is; a plain A2A card is mapped
  (`card_from_a2a`); tasks always go over A2A.

Each provider is an opaque endpoint + agent card; nothing else is assumed about it. A provider
whose card can't be fetched or parsed is left out of the run with the reason logged and kept on
the returned `Registry` (`unlisted`, by card URL), so the A2A door can report it (AGE-60). The MVP
has no Supabase tables (D21), so the registry is the `Store`'s provider list.
"""

import asyncio
import logging
import re
from collections.abc import Iterable
from typing import Any

import httpx
from pydantic import ValidationError

from contracts import AgentCard, RegisteredProvider
from core.pipeline.config import (
    Transport,
    provider_a2a_urls,
    provider_slugs,
    provider_transport,
    providers_base_url,
)
from core.store import Store

log = logging.getLogger(__name__)

CARD_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json")
_NOT_SLUG = re.compile(r"[^a-z0-9-]+")
Unlisted = tuple[str, str]


class Registry(list[RegisteredProvider]):
    """The providers a run can consider, plus `unlisted`: `(id, reason)` for each source that
    couldn't be listed, where `id` is the card URL (or the `slug@version` listed twice)."""

    def __init__(
        self, providers: Iterable[RegisteredProvider] = (), unlisted: Iterable[Unlisted] = ()
    ) -> None:
        super().__init__(providers)
        self.unlisted: list[Unlisted] = list(unlisted)


def _why(exc: Exception) -> str:
    """A one-line reason for a failed card fetch (httpx's own messages run over several lines)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"card fetch answered HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return f"card fetch failed ({type(exc).__name__})"
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "card"
        return f"card doesn't parse ({exc.error_count()} errors; first at {where}: {first['msg']})"
    return f"card doesn't parse ({str(exc).splitlines()[0] if str(exc) else type(exc).__name__})"


def card_url(base_url: str, slug: str) -> str:
    return f"{base_url.rstrip('/')}/p/{slug}/.well-known/agent-card.json"


def a2a_url(base_url: str, slug: str) -> str:
    """A provider's A2A base URL in our layout (wire contracts §3)."""
    return f"{base_url.rstrip('/')}/p/{slug}/a2a"


def slugify(name: str) -> str:
    """Lowercase; runs of characters outside `[a-z0-9-]` → `-`; leading `-` trimmed."""
    return _NOT_SLUG.sub("-", name.lower()).lstrip("-")


def card_from_a2a(data: Any) -> AgentCard:
    """Our `AgentCard` for an A2A card: as is if it has our fields, else mapped from a plain card.

    Mapping: slug = the card's `slug` key, else `name` slugified; `job_types` = skill ids;
    `description` = the card's or ""; price and region unknown. Raises `ValueError` (a pydantic
    `ValidationError` included) when the result isn't a valid card, e.g. a bad slug or version."""
    if not isinstance(data, dict):
        raise ValueError("card is not a JSON object")
    try:
        return AgentCard.model_validate(data)
    except ValidationError:
        pass
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("plain A2A card has no name")
    slug = data.get("slug") if isinstance(data.get("slug"), str) else slugify(name)
    skills = data.get("skills")
    job_types = [
        s["id"]
        for s in (skills if isinstance(skills, list) else [])
        if isinstance(s, dict) and isinstance(s.get("id"), str)
    ]
    description = data.get("description")
    return AgentCard.model_validate(
        {
            "slug": slug,
            "name": name,
            "description": description if isinstance(description, str) else "",
            "version": data.get("version"),
            "job_types": job_types,
            "price_per_doc_usd": None,
            "region": None,
        }
    )


async def _fetch(
    client: httpx.AsyncClient, base_url: str, slug: str, transport: Transport
) -> RegisteredProvider | Unlisted:
    url = card_url(base_url, slug)
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        card = AgentCard.model_validate_json(resp.content)
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("provider %s left out: card at %s unavailable (%s)", slug, url, exc)
        return url, _why(exc)
    if transport == "a2a":
        return RegisteredProvider(
            card=card, base_url=base_url, transport="a2a", a2a_url=a2a_url(base_url, slug)
        )
    return RegisteredProvider(card=card, base_url=base_url)


async def _fetch_a2a(client: httpx.AsyncClient, url: str) -> RegisteredProvider | Unlisted:
    """The agent at A2A base URL `url`, or (card URL, reason), logged, if its card is missing
    or unusable."""
    url = url.rstrip("/")
    tried = ""
    try:
        for path in CARD_PATHS:
            tried = url + path
            resp = await client.get(tried)
            if resp.status_code not in (404, 410):
                break
        resp.raise_for_status()
        card = card_from_a2a(resp.json())
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("A2A agent %s left out: card at %s unusable (%s)", url, tried, exc)
        return tried, _why(exc)
    return RegisteredProvider(card=card, base_url=url, transport="a2a", a2a_url=url)


async def refresh_registry(
    store: Store | None = None,
    *,
    base_url: str | None = None,
    slugs: list[str] | None = None,
    a2a_urls: list[str] | None = None,
    via: Transport | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout_s: float = 10.0,
) -> Registry:
    """Fetch every card at once. Defaults: `PROVIDERS_BASE_URL`, `PROVIDER_SLUGS`,
    `PROVIDER_A2A_URLS` and `PROVIDER_TRANSPORT` (`via`; `transport` is the httpx
    transport, for tests).

    Providers that can't be listed are logged and left out (and named in `Registry.unlisted`);
    the rest are returned in slug order (and upserted into `store`, if given). If two sources
    give the same `slug@version`, the first one (slugs before A2A URLs) is kept."""
    base_url = base_url or providers_base_url()
    slugs = provider_slugs() if slugs is None else slugs
    a2a_urls = provider_a2a_urls() if a2a_urls is None else a2a_urls
    mode = via or provider_transport()
    async with httpx.AsyncClient(transport=transport, timeout=timeout_s) as client:
        fetched = await asyncio.gather(
            *(_fetch(client, base_url, s, mode) for s in slugs),
            *(_fetch_a2a(client, u) for u in a2a_urls),
        )
    unique: dict[str, RegisteredProvider] = {}
    unlisted: list[Unlisted] = []
    for p in fetched:
        if isinstance(p, tuple):
            unlisted.append(p)
        elif p.provider_id in unique:
            log.warning("provider %s left out: listed twice", p.provider_id)
            unlisted.append((p.provider_id, "listed twice"))
        else:
            unique[p.provider_id] = p
    providers = sorted(unique.values(), key=lambda p: (p.card.slug, p.card.version))
    if store is not None:
        for p in providers:
            store.upsert_provider(p)
    return Registry(providers, unlisted)
