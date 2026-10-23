"""The Candidate Finder (AGE-14, plan §3 #5): which registered providers get the trial.

A provider is kept when its agent card declares the job type, its list price fits the per-doc
budget (D19) and, if the job sets a region, it serves that region or "global". The reputation
threshold is a no-op in the MVP (no ledger yet). Each provider left out is logged with the reason.
"""

import logging

from contracts import JobSpec, RegisteredProvider

log = logging.getLogger(__name__)

JOB_TYPE = "invoice_extraction"


def rejection_reason(spec: JobSpec, p: RegisteredProvider) -> str | None:
    """Why `p` doesn't qualify for `spec`, or None if it does."""
    card, job_type = p.card, spec.job_type or JOB_TYPE
    if job_type not in card.job_types:
        return f"doesn't declare job type {job_type!r} (declares {card.job_types})"
    if card.price_per_doc_usd > spec.per_doc_budget_usd:
        return (
            f"price ${card.price_per_doc_usd:.4f}/doc exceeds the budget "
            f"${spec.per_doc_budget_usd:.4f}/doc"
        )
    if spec.region is not None and card.region not in (spec.region, "global"):
        return f"region {card.region!r} doesn't serve {spec.region!r}"
    return None


def find_candidates(spec: JobSpec, registry: list[RegisteredProvider]) -> list[RegisteredProvider]:
    """The providers in `registry` that qualify for `spec`, sorted by slug."""
    kept = []
    for p in registry:
        reason = rejection_reason(spec, p)
        if reason is None:
            kept.append(p)
        else:
            log.info("job %s: provider %s filtered out: %s", spec.job_id, p.provider_id, reason)
    return sorted(kept, key=lambda p: p.card.slug)
