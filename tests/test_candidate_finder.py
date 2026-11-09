import logging

import pytest

from contracts import JobPosting, JobSpec, RegisteredProvider
from core.pipeline.stubs import review_job_stub
from core.runner.candidate_finder import find_candidates, rejection_reason
from core.runner.registry import card_from_a2a
from examples.make_examples import EXAMPLES_DIR
from tests.test_contracts.examples import REGISTERED_PROVIDER
from tests.test_registry import TOWN_CARD


def provider(slug: str, **card) -> RegisteredProvider:
    p = REGISTERED_PROVIDER  # job type invoice_extraction, region "global", price 0.01
    return p.model_copy(update={"card": p.card.model_copy(update={"slug": slug} | card)})


def spec(**kw) -> JobSpec:
    base = dict(
        job_id="j",
        decision="approve",
        reason="",
        job_type="invoice_extraction",
        n_real_docs=10,
        per_doc_budget_usd=0.05,
    )
    return JobSpec(**(base | kw))


def slugs(providers: list[RegisteredProvider]) -> list[str]:
    return [p.card.slug for p in providers]


@pytest.mark.parametrize("name", ["invoice_clean", "invoice_long"])
def test_all_seven_qualify_for_the_example_postings(name):
    posting = JobPosting.model_validate_json((EXAMPLES_DIR / f"{name}.json").read_text())
    job = review_job_stub(posting)  # same per-doc budget and region as the real reviewer
    registry = [provider(f"p{i}", price_per_doc_usd=0.01 * i) for i in range(7, 0, -1)]
    assert slugs(find_candidates(job, registry)) == [f"p{i}" for i in range(1, 8)]


def test_price_filter_keeps_providers_at_or_under_the_per_doc_budget():
    registry = [
        provider("under", price_per_doc_usd=0.04),
        provider("equal", price_per_doc_usd=0.05),
        provider("over", price_per_doc_usd=0.0501),
    ]
    assert slugs(find_candidates(spec(), registry)) == ["equal", "under"]
    assert slugs(find_candidates(spec(per_doc_budget_usd=0.0), registry)) == []


def test_region_filter_matches_the_region_or_global():
    registry = [provider("eu", region="eu"), provider("us", region="us"), provider("world")]
    assert slugs(find_candidates(spec(), registry)) == ["eu", "us", "world"]  # no region set
    assert slugs(find_candidates(spec(region="eu"), registry)) == ["eu", "world"]
    assert slugs(find_candidates(spec(region="apac"), registry)) == ["world"]


def test_job_type_filter():
    registry = [provider("other", job_types=["translation"]), provider("inv")]
    assert slugs(find_candidates(spec(), registry)) == ["inv"]


def test_each_filtered_out_provider_is_logged_with_the_reason(caplog):
    registry = [
        provider("pricey", price_per_doc_usd=1.0),
        provider("far", region="eu"),
        provider("other", job_types=[]),
    ]
    with caplog.at_level(logging.INFO, logger="core.runner.candidate_finder"):
        assert find_candidates(spec(region="us"), registry) == []
    assert "pricey@1.0.0 filtered out: price $1.0000/doc exceeds" in caplog.text
    assert "far@1.0.0 filtered out: region 'eu'" in caplog.text
    assert "other@1.0.0 filtered out: doesn't declare job type" in caplog.text


# --- unknown price / region (AGE-59) ----------------------------------------------------------


def test_unknown_price_fails_the_budget_filter_with_a_reason():
    p = provider("plain", price_per_doc_usd=None)
    assert rejection_reason(spec(), p) == "no list price on its card"
    assert find_candidates(spec(), [p, provider("priced")]) == [provider("priced")]


def test_unknown_region_serves_no_requested_region():
    p = provider("nowhere", region=None)
    assert slugs(find_candidates(spec(), [p])) == ["nowhere"]  # no region requested
    assert find_candidates(spec(region="eu"), [p]) == []


def test_town_agent_is_excluded_on_job_type_before_price():
    card = card_from_a2a(TOWN_CARD)
    town = RegisteredProvider(
        card=card, base_url="http://town", transport="a2a", a2a_url="http://town"
    )
    assert rejection_reason(spec(), town) == (
        "doesn't declare job type 'invoice_extraction' (declares ['quote'])"
    )
