"""HW7 baselines (AGE-35): profile-reading AI pick and cheapest pick."""

import json

import pytest

from contracts import AgentCard, JobPosting
from experiments.hw7.baselines import (
    COMPONENT,
    MAX_ATTEMPTS,
    MAX_TOKENS,
    SYSTEM_PROMPT,
    ProfilePick,
    pick_by_profile,
    pick_cheapest,
)
from experiments.hw7.make_jobs import JOBS_DIR
from gateway import DEFAULT_MODEL, Gateway, JsonlCostSink
from mocks.fake_gateway import FakeGateway


def card(slug: str, price: float, **kw) -> AgentCard:
    return AgentCard(
        slug=slug,
        name=kw.get("name", f"Agent {slug}"),
        description=kw.get("description", "Extracts invoice fields to JSON."),
        version="1",
        job_types=["invoice_extraction"],
        price_per_doc_usd=price,
        region=kw.get("region", "global"),
    )


CARDS = [card("alpha", 0.05), card("bravo", 0.02), card("charlie", 0.10)]


def posting(job: str = "j01") -> JobPosting:
    return JobPosting.model_validate_json((JOBS_DIR / f"{job}.json").read_text())


def pick(*replies) -> tuple[ProfilePick, FakeGateway]:
    gw = FakeGateway(*replies)
    return pick_by_profile(posting(), CARDS, gateway=gw, job_id="hw7-j01-r1"), gw  # type: ignore[arg-type]


def test_valid_pick():
    p, gw = pick({"slug": "charlie", "reason": "Best fit for clean invoices."})
    assert p == ProfilePick(
        slug="charlie",
        reason="Best fit for clean invoices.",
        attempts=1,
        failed=False,
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        latency_ms=p.latency_ms,
    )
    assert p.latency_ms >= 0
    (call,) = gw.calls
    assert call["component"] == COMPONENT == "baseline_profile"
    assert call["model"] == DEFAULT_MODEL
    assert call["response_format"] == {"type": "json_object"}
    assert call["max_tokens"] == MAX_TOKENS == 600
    assert call["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}


def test_prompt_has_only_posting_budget_and_card_fields():
    _, gw = pick({"slug": "alpha", "reason": "ok"})
    user = gw.calls[0]["messages"][1]["content"]
    job_part, cards_part = user.split("Candidate agents:\n")
    job = json.loads(job_part.removeprefix("Job posting:\n"))
    p = posting()
    assert job == {
        "description": p.description,
        "output_description": p.output_description,
        "budget_per_doc_usd": round(p.job_budget_usd / p.n_real_docs, 4),
    }
    profiles = json.loads(cards_part)
    assert [set(c) for c in profiles] == [
        {"slug", "name", "description", "job_types", "price_per_doc_usd", "region"}
    ] * len(CARDS)
    assert [c["slug"] for c in profiles] == ["alpha", "bravo", "charlie"]
    assert "samples" not in user and ".pdf" not in user and "version" not in user


def test_bad_json_then_valid_takes_two_attempts():
    p, gw = pick("not json", {"slug": "bravo", "reason": "Cheap and fine."})
    assert (p.slug, p.attempts, p.failed) == ("bravo", 2, False)
    assert len(gw.calls) == 2


def test_empty_reply_is_retried():
    p, _ = pick("", {"slug": "alpha", "reason": "ok"})
    assert (p.slug, p.attempts) == ("alpha", 2)


def test_unknown_slug_three_times_fails():
    bad = {"slug": "delta", "reason": "not a candidate"}
    p, gw = pick(bad, bad, bad)
    assert p.failed and p.slug is None and p.reason == ""
    assert p.attempts == MAX_ATTEMPTS == 3
    assert len(gw.calls) == 3


def test_missing_reason_is_empty_string():
    p, _ = pick({"slug": "alpha"})
    assert (p.slug, p.reason, p.failed) == ("alpha", "", False)


def test_pick_cheapest_breaks_ties_by_slug():
    cards = [card("zulu", 0.01), card("mike", 0.03), card("echo", 0.01)]
    assert pick_cheapest(cards) == "echo"
    assert pick_cheapest(CARDS) == "bravo"


def test_empty_cards_rejected():
    with pytest.raises(ValueError):
        pick_cheapest([])
    with pytest.raises(ValueError):
        pick_by_profile(posting(), [], gateway=FakeGateway(), job_id="x")  # type: ignore[arg-type]


@pytest.mark.live
def test_live_pick_one_job(tmp_path, capsys):
    cards = [
        card("alpha", 0.004, description="General document extraction for any invoice."),
        card("bravo", 0.010, description="Invoice extraction to JSON with line items."),
        card("charlie", 0.030, description="Premium invoice extraction, careful with tables."),
    ]
    gw = Gateway(JsonlCostSink(tmp_path / "llm_calls.jsonl"), account="live-test")
    p = pick_by_profile(posting("j01"), cards, gateway=gw, job_id="hw7-live-j01")
    with capsys.disabled():
        print(
            f"\nbaseline_profile live: slug={p.slug} attempts={p.attempts} "
            f"cost=${p.cost_usd:.5f} tokens={p.tokens_in}+{p.tokens_out} "
            f"latency={p.latency_ms}ms reason={p.reason!r}"
        )
    assert not p.failed, p
    assert p.slug in {c.slug for c in cards}
    assert p.cost_usd > 0
