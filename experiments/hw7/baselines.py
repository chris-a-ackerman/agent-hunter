"""HW7 comparison baselines (AGE-35, experiments/hw7/PREREG.md §4, plan §3 #13).

- `pick_by_profile`: an LLM reads the posting and each provider's public agent card and picks one,
  with no samples and no trial. Roughly how agents choose providers today.
- `pick_cheapest`: lowest list price, ties broken by slug.
- Random needs no code: the analysis uses its expected value over all providers.

The harness (AGE-36) calls these and logs the picks.
"""

import json
import logging
import time
from dataclasses import dataclass

from contracts import AgentCard, JobPosting
from gateway import DEFAULT_MODEL, Gateway

log = logging.getLogger(__name__)

COMPONENT = "baseline_profile"
MAX_TOKENS = 600  # gpt-6-luna's reasoning truncates the answer below this
MAX_ATTEMPTS = 3  # 1 call + up to 2 retries

SYSTEM_PROMPT = """\
You hire an AI agent to do a document-processing job for a requester. You get the job posting, \
the budget per document and the public profile of each candidate agent (slug, name, description, \
job types, price per document and region). Pick the one agent most likely to do this job best \
within budget.

Reply with a JSON object only:
{"slug": "<the chosen agent's slug, exactly as given>", "reason": "<1-2 sentences>"}"""


@dataclass
class ProfilePick:
    slug: str | None  # None when every attempt failed
    reason: str  # the model's 1–2 sentences ("" on failure)
    attempts: int  # 1–3
    failed: bool
    tokens_in: int
    tokens_out: int
    cost_usd: float  # summed over attempts
    latency_ms: int  # summed over attempts


def _profile(card: AgentCard) -> dict:
    return {
        "slug": card.slug,
        "name": card.name,
        "description": card.description,
        "job_types": card.job_types,
        "price_per_doc_usd": card.price_per_doc_usd,
        "region": card.region,
    }


def user_message(posting: JobPosting, cards: list[AgentCard]) -> str:
    """The only information the model gets: no sample PDFs, no trial results."""
    job = {
        "description": posting.description,
        "output_description": posting.output_description,
        "budget_per_doc_usd": round(posting.job_budget_usd / posting.n_real_docs, 4),
    }
    return (
        f"Job posting:\n{json.dumps(job, indent=2)}\n\n"
        f"Candidate agents:\n{json.dumps([_profile(c) for c in cards], indent=2)}"
    )


def pick_by_profile(
    posting: JobPosting, cards: list[AgentCard], *, gateway: Gateway, job_id: str
) -> ProfilePick:
    """Ask the model to pick a provider from the cards alone. Bad JSON, an empty reply or an
    unknown slug is retried, up to `MAX_ATTEMPTS` calls; then `failed=True, slug=None`."""
    if not cards:
        raise ValueError("pick_by_profile needs at least one card")
    slugs = {c.slug for c in cards}
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message(posting, cards)},
    ]
    tokens_in = tokens_out = latency_ms = 0
    cost = 0.0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        start = time.perf_counter()
        resp = gateway.call(
            COMPONENT,
            DEFAULT_MODEL,
            messages,
            response_format={"type": "json_object"},
            max_tokens=MAX_TOKENS,
            job_id=job_id,
        )
        latency_ms += round((time.perf_counter() - start) * 1000)
        tokens_in += resp.tokens_in
        tokens_out += resp.tokens_out
        cost += resp.cost_usd

        parsed = resp.parsed
        slug = parsed.get("slug") if parsed else None
        if isinstance(slug, str) and slug in slugs:
            reason = parsed.get("reason") if parsed else None
            return ProfilePick(
                slug=slug,
                reason=reason.strip() if isinstance(reason, str) else "",
                attempts=attempt,
                failed=False,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                latency_ms=latency_ms,
            )
        problem = (
            "empty reply"
            if not resp.content.strip()
            else "not a JSON object"
            if parsed is None
            else f"unknown slug {slug!r}"
        )
        log.warning(
            "baseline_profile: attempt %d/%d for job %s unusable (%s)",
            attempt,
            MAX_ATTEMPTS,
            job_id,
            problem,
        )
    return ProfilePick(
        slug=None,
        reason="",
        attempts=MAX_ATTEMPTS,
        failed=True,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        latency_ms=latency_ms,
    )


def pick_cheapest(cards: list[AgentCard]) -> str:
    """Lowest price_per_doc_usd; ties broken by slug."""
    if not cards:
        raise ValueError("pick_cheapest needs at least one card")
    return min(cards, key=lambda c: (c.price_per_doc_usd, c.slug)).slug
