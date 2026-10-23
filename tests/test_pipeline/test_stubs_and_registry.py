import httpx

from contracts import JobSpec, Submission
from core.pipeline import stubs
from core.pipeline.registry import load_registry
from mocks.fake_providers import card
from tests.test_contracts.examples import REGISTERED_PROVIDER, SCORE_REPORT


def test_load_registry_fetches_cards_skips_broken_ones_and_upserts(store):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/p/mock-a/.well-known/agent-card.json":
            return httpx.Response(200, json=card("mock-a").model_dump())
        if request.url.path == "/p/bad/.well-known/agent-card.json":
            return httpx.Response(200, json={"slug": "bad"})
        return httpx.Response(404)

    got = load_registry(
        "http://p",
        ["mock-a", "missing", "bad"],
        store=store,
        transport=httpx.MockTransport(handler),
    )
    assert [p.provider_id for p in got] == ["mock-a@1"]
    assert got[0].tasks_url == "http://p/p/mock-a/tasks"
    assert [p.provider_id for p in store.list_providers()] == ["mock-a@1"]


def _spec(**kw) -> JobSpec:
    base = dict(
        job_id="j",
        decision="approve",
        reason="",
        job_type="invoice_extraction",
        n_real_docs=10,
        per_doc_budget_usd=0.5,
    )
    return JobSpec(**(base | kw))


def test_find_candidates_filters_job_type_region_and_price():
    p = REGISTERED_PROVIDER  # region "global", price 0.01
    other_region = p.model_copy(update={"card": p.card.model_copy(update={"region": "eu"})})
    pricey = p.model_copy(update={"card": p.card.model_copy(update={"price_per_doc_usd": 1.0})})
    wrong_type = p.model_copy(update={"card": p.card.model_copy(update={"job_types": ["x"]})})
    reg = [p, other_region, pricey, wrong_type]
    assert stubs.find_candidates_stub(_spec(), reg) == [p, other_region]
    assert stubs.find_candidates_stub(_spec(region="us"), reg) == [p]
    assert stubs.find_candidates_stub(_spec(region="eu"), reg) == [p, other_region]


def test_rank_stub_with_one_provider_is_insufficient():
    sub = Submission(
        task_id="t",
        doc_id=SCORE_REPORT.doc_scores[0].doc_id,
        provider=SCORE_REPORT.provider,
        status="ok",
        output={},
        latency_ms=10,
        cost_usd=0.01,
    )
    v = stubs.rank_stub([SCORE_REPORT], [sub], "trial-x")
    assert v.status == "insufficient_candidates" and v.recommended is None
    assert v.ranking[0].cost_per_doc == 0.01 and v.evidence == "trial-x"
