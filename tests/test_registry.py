import asyncio

import httpx
import pytest

from core.pipeline.config import provider_transport
from core.runner.registry import card_from_a2a, refresh_registry, slugify
from core.store import JsonStore
from mocks.fake_providers import card, create_app


def handler(request: httpx.Request) -> httpx.Response:
    slug = request.url.path.split("/")[2]
    if slug.startswith("mock-"):
        return httpx.Response(200, json=card(slug).model_dump())
    if slug == "bad":
        return httpx.Response(200, json={"slug": "bad"})
    return httpx.Response(404)


def refresh(store=None, **kw):
    kw.setdefault("transport", httpx.MockTransport(handler))
    return asyncio.run(refresh_registry(store, base_url="http://p", **kw))


def test_fetches_cards_skips_broken_ones_and_upserts(tmp_path, caplog):
    store = JsonStore(tmp_path / "store")
    got = refresh(store, slugs=["mock-b", "missing", "bad", "mock-a"])
    assert [p.provider_id for p in got] == ["mock-a@1", "mock-b@1"]  # sorted by slug
    assert got[0].tasks_url == "http://p/p/mock-a/tasks"
    assert [p.provider_id for p in store.list_providers()] == ["mock-a@1", "mock-b@1"]
    assert "missing left out" in caplog.text and "bad left out" in caplog.text


def test_unreachable_provider_is_skipped_not_fatal():
    def flaky(request: httpx.Request) -> httpx.Response:
        if "mock-a" in request.url.path:
            raise httpx.ConnectError("refused")
        return handler(request)

    got = refresh(slugs=["mock-a", "mock-b"], transport=httpx.MockTransport(flaky))
    assert [p.provider_id for p in got] == ["mock-b@1"]


def test_slugs_default_to_env(monkeypatch):
    monkeypatch.setenv("PROVIDER_SLUGS", "mock-c, mock-a")
    assert [p.card.slug for p in refresh()] == ["mock-a", "mock-c"]


# --- A2A (AGE-59) -----------------------------------------------------------------------------

TOWN_CARD = {
    "name": "nandatown-quote-seller",
    "description": "Reference quote seller",
    "url": "http://town/",
    "version": "0.3.1",
    "capabilities": {"streaming": False, "pushNotifications": False},
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text"],
    "skills": [{"id": "quote", "name": "Quote", "tags": ["quote", "commerce", "nandatown"]}],
}


def a2a_cards(cards: dict[str, httpx.Response]) -> httpx.MockTransport:
    """Answers GET {url} from `cards`, 404 otherwise."""
    return httpx.MockTransport(lambda r: cards.get(str(r.url), httpx.Response(404)))


def refresh_a2a(urls, transport, **kw):
    return asyncio.run(
        refresh_registry(base_url="http://p", slugs=[], a2a_urls=urls, transport=transport, **kw)
    )


def test_a2a_card_with_our_fields_is_used_as_is():
    app = create_app(base_url="http://mocks")
    got = refresh_a2a(["http://mocks/p/mock-a/a2a/"], httpx.ASGITransport(app=app))
    assert len(got) == 1
    p = got[0]
    assert p.card == card("mock-a")  # price and region come from the card's extra keys
    assert p.provider_id == "mock-a@1"
    assert (p.transport, p.a2a_url, p.base_url) == ("a2a", "http://mocks/p/mock-a/a2a", p.a2a_url)


def test_plain_a2a_card_is_mapped_from_agent_json_fallback():
    transport = a2a_cards(
        {"http://town/.well-known/agent.json": httpx.Response(200, json=TOWN_CARD)}
    )  # agent-card.json answers 404
    [p] = refresh_a2a(["http://town"], transport)
    c = p.card
    assert (c.slug, c.name, c.version, c.job_types) == (
        "nandatown-quote-seller",
        "nandatown-quote-seller",
        "0.3.1",
        ["quote"],
    )
    assert (c.description, c.price_per_doc_usd, c.region) == ("Reference quote seller", None, None)
    assert (p.provider_id, p.transport, p.a2a_url) == (
        "nandatown-quote-seller@0.3.1",
        "a2a",
        "http://town",
    )


def test_fallback_also_on_410_but_not_on_other_errors(caplog):
    gone = a2a_cards(
        {
            "http://a/.well-known/agent-card.json": httpx.Response(410),
            "http://a/.well-known/agent.json": httpx.Response(200, json=TOWN_CARD),
            "http://b/.well-known/agent-card.json": httpx.Response(500),
            "http://b/.well-known/agent.json": httpx.Response(200, json=TOWN_CARD),
        }
    )
    got = refresh_a2a(["http://a", "http://b"], gone)
    assert [p.a2a_url for p in got] == ["http://a"]
    assert "A2A agent http://b left out" in caplog.text


def test_plain_card_mapping_rules():
    named = card_from_a2a(TOWN_CARD | {"name": "  Acme Invoice_Bot 2!", "description": None})
    assert (named.slug, named.description) == ("acme-invoice-bot-2-", "")
    assert card_from_a2a(TOWN_CARD | {"slug": "acme"}).slug == "acme"  # slug key wins
    assert card_from_a2a(TOWN_CARD | {"skills": []}).job_types == []
    assert slugify("--Foo  Bar--") == "foo-bar--"


@pytest.mark.parametrize(
    "bad",
    [
        TOWN_CARD | {"version": "1 2"},  # version must match ^[^@\s]+$
        TOWN_CARD | {"version": "1@2"},
        TOWN_CARD | {"name": "!!!"},  # slugifies to ""
        TOWN_CARD | {"slug": "Not A Slug"},
        {k: v for k, v in TOWN_CARD.items() if k != "name"},
        {k: v for k, v in TOWN_CARD.items() if k != "version"},
        ["not", "an", "object"],
    ],
)
def test_bad_a2a_cards_are_left_out_with_a_reason(bad, caplog):
    transport = a2a_cards({"http://x/.well-known/agent-card.json": httpx.Response(200, json=bad)})
    assert refresh_a2a(["http://x"], transport) == []
    assert (
        "A2A agent http://x left out: card at http://x/.well-known/agent-card.json" in caplog.text
    )


def test_unparseable_a2a_card_is_left_out(caplog):
    transport = a2a_cards(
        {"http://x/.well-known/agent-card.json": httpx.Response(200, content=b"<html>")}
    )
    assert refresh_a2a(["http://x"], transport) == []
    assert "left out" in caplog.text


def test_http_is_the_default_transport(monkeypatch):
    monkeypatch.delenv("PROVIDER_TRANSPORT", raising=False)
    [p] = refresh(slugs=["mock-a"])
    assert (p.transport, p.a2a_url) == ("http", None)


def test_provider_transport_a2a_keeps_the_http_card_and_points_at_a2a(monkeypatch):
    monkeypatch.setenv("PROVIDER_TRANSPORT", "a2a")
    [p] = refresh(slugs=["mock-a"])
    assert p.card == card("mock-a")
    assert (p.transport, p.a2a_url, p.base_url) == ("a2a", "http://p/p/mock-a/a2a", "http://p")


def test_provider_transport_must_be_http_or_a2a(monkeypatch):
    monkeypatch.setenv("PROVIDER_TRANSPORT", "grpc")
    with pytest.raises(ValueError, match="PROVIDER_TRANSPORT"):
        provider_transport()


def test_a2a_urls_default_to_env_and_join_the_slugs(monkeypatch):
    monkeypatch.setenv("PROVIDER_SLUGS", "mock-b")
    monkeypatch.setenv("PROVIDER_A2A_URLS", " http://town/ , ")

    def both(request: httpx.Request) -> httpx.Response:
        if request.url.host == "town":
            if request.url.path == "/.well-known/agent-card.json":
                return httpx.Response(200, json=TOWN_CARD)
            return httpx.Response(404)
        return handler(request)

    got = asyncio.run(refresh_registry(base_url="http://p", transport=httpx.MockTransport(both)))
    assert [(p.provider_id, p.transport) for p in got] == [
        ("mock-b@1", "http"),
        ("nandatown-quote-seller@0.3.1", "a2a"),
    ]


def test_same_provider_from_two_sources_is_listed_once(caplog):
    app = create_app(base_url="http://p")
    got = asyncio.run(
        refresh_registry(
            base_url="http://p",
            slugs=["mock-a"],
            a2a_urls=["http://p/p/mock-a/a2a"],
            transport=httpx.ASGITransport(app=app),
        )
    )
    assert [(p.provider_id, p.transport) for p in got] == [("mock-a@1", "http")]
    assert "mock-a@1 left out: listed twice" in caplog.text
