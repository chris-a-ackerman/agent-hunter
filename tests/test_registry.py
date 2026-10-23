import asyncio

import httpx

from core.runner.registry import refresh_registry
from core.store import JsonStore
from mocks.fake_providers import card


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
