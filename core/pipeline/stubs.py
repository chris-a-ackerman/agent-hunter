"""Stand-ins for the steps that don't exist yet, so the pipeline runs end to end today.

Each one satisfies its `contracts.steps` Protocol and is deliberately simple. `config.build_steps`
swaps each for the real step once it lands:

    review_job_stub       → Job Reviewer (AGE-13, landed; the stub stays for offline tests)
    build_rubric_stub     → Rubric Builder (AGE-21)
    SampleTrialBuilder    → Trial Builder (AGE-22)
    rank_stub             → Ranker (AGE-24)

`offline_steps` wires them with the mock providers and the real Scorer for the A2A door's
offline mode (AGE-45): a whole hire with no model call and no Parley key.
"""

import functools
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import TYPE_CHECKING, Any, NoReturn

from contracts import (
    JobPosting,
    JobSpec,
    RankingRow,
    Rubric,
    ScoreReport,
    Submission,
    TrialDoc,
    TrialPackage,
    Verdict,
    new_doc_id,
)
from contracts.rubric import SCORED_FIELDS
from core.eval.defaults import DEFAULT_WEIGHTS
from core.eval.stats import decide

if TYPE_CHECKING:
    from core.pipeline.runner import Steps
    from core.store import Store

JOB_TYPE = "invoice_extraction"

# --- Job Reviewer -----------------------------------------------------------------------------


def review_job_stub(posting: JobPosting) -> JobSpec:
    """No output schema → needs_info; a schema without the scored invoice fields → reject."""
    common = {
        "job_id": "unassigned",  # ReviewJob doesn't get the job ID; run_pipeline sets it
        "n_real_docs": posting.n_real_docs,
        "per_doc_budget_usd": posting.job_budget_usd / posting.n_real_docs,
        "region": posting.region,
        "notes": "stub Job Reviewer (AGE-13 pending)",
    }
    if posting.output_schema is None:
        return JobSpec(
            decision="needs_info",
            reason="The posting has no output schema.",
            questions=["What JSON schema should each extracted document match?"],
            **common,
        )
    props = posting.output_schema.get("properties")
    missing = sorted(set(SCORED_FIELDS) - set(props if isinstance(props, dict) else {}))
    if missing:
        return JobSpec(
            decision="reject",
            reason=f"Not an invoice-extraction job we can score: the output schema lacks "
            f"{', '.join(missing)}.",
            **common,
        )
    return JobSpec(
        decision="approve",
        reason="Invoice extraction to the invoice schema.",
        job_type=JOB_TYPE,
        fields=list(SCORED_FIELDS),
        **common,
    )


# --- Rubric Builder ---------------------------------------------------------------------------


def build_rubric_stub(spec: JobSpec) -> Rubric:
    """Plan §8 default weights, default tolerances, auto-approved (D6)."""
    return Rubric(
        rubric_id=f"rubric-{uuid.uuid4().hex[:12]}",
        job_id=spec.job_id,
        weights=dict(DEFAULT_WEIGHTS),
        required_fields=["vendor_name", "invoice_date", "currency", "total"],
        approved_at=datetime.now(UTC),
    )


# --- Trial Builder ----------------------------------------------------------------------------


class SampleTrialBuilder:
    """A trial made of the posting's samples only.

    Each sample PDF is copied to `docs_dir/{doc_id}.pdf` (served by the doc server, D17) and its
    answer written to `truth_dir/{doc_id}.json` (read only by the mock providers). Sample paths
    are relative to `samples_dir`, the directory the posting files live in.
    """

    def __init__(self, samples_dir: Path, docs_dir: Path, truth_dir: Path) -> None:
        self.samples_dir, self.docs_dir, self.truth_dir = samples_dir, docs_dir, truth_dir

    def __call__(self, spec: JobSpec, posting: JobPosting) -> TrialPackage:
        samples = [s for s in posting.samples if s.answer is not None]
        if not samples:
            raise ValueError("the stub Trial Builder needs at least one sample with an answer")
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.truth_dir.mkdir(parents=True, exist_ok=True)
        docs = []
        for s in samples:
            assert s.answer is not None
            doc_id = new_doc_id()
            shutil.copyfile(self.samples_dir / s.path, self.docs_dir / f"{doc_id}.pdf")
            (self.truth_dir / f"{doc_id}.json").write_text(s.answer.model_dump_json(indent=2))
            docs.append(TrialDoc(doc_id=doc_id, source="sample", answer=s.answer))
        return TrialPackage(
            trial_id=f"trial-{uuid.uuid4().hex[:12]}",
            job_id=spec.job_id,
            documents=docs,
            created_at=datetime.now(UTC),
        )


# --- Ranker -----------------------------------------------------------------------------------


def rank_stub(reports: list[ScoreReport], submissions: list[Submission], trial_id: str) -> Verdict:
    """Ranking rows per plan §8 / D19 and the decision from `core.eval.stats.decide`, with a
    template rationale instead of the Ranker's LLM one."""
    rows = []
    for r in reports:
        subs = [s for s in submissions if s.provider == r.provider]
        costs = [s.cost_usd for s in subs if s.status != "not_run" and s.cost_usd is not None]
        latencies = [s.latency_ms for s in subs if s.status == "ok" and s.latency_ms is not None]
        rows.append(
            RankingRow(
                provider=r.provider,
                accuracy=r.trial_score,
                ci_95=r.ci_95,
                cost_per_doc=fmean(costs) if costs else None,
                timeouts=sum(s.status == "timeout" for s in subs),
                mean_latency_ms=fmean(latencies) if latencies else None,
            )
        )
    rows.sort(key=lambda row: -row.accuracy)
    d = decide(rows)
    best = next((row for row in rows if row.provider == d.winner), None)
    if d.status == "insufficient_candidates":
        rationale = "Fewer than two providers answered the trial, so there is no recommendation."
    elif d.status == "tie":
        rationale = (
            f"{len(d.tied)} providers have overlapping confidence intervals; {d.winner} wins "
            f"the tie on cost per document, then latency."
        )
    else:
        rationale = f"{d.winner} leads and its confidence interval clears the runner-up's."
    return Verdict(
        status=d.status,
        recommended=d.winner,
        tied=d.tied,
        trial_accuracy=best.accuracy if best else None,
        ci_95=best.ci_95 if best else None,
        cost_per_doc=best.cost_per_doc if best else None,
        valid_until=None,
        tie=d.status == "tie",
        ranking=rows,
        rationale=rationale,
        evidence=trial_id,
    )


# --- Offline mode (AGE-45) --------------------------------------------------------------------


class ModelCallForbidden(RuntimeError):
    """Raised by any model call while offline mode forbids them."""


def _refuse(*_: Any, **__: Any) -> NoReturn:
    raise ModelCallForbidden("offline mode makes no model calls")


class NoModelGateway:
    """A gateway whose every call raises, so an accidental model call fails loudly."""

    call = staticmethod(_refuse)


def forbid_model_calls() -> None:
    """Make every model call in this process raise: the gateway class and the default-gateway
    factory every step falls back to. For the offline server process only (it isn't undone)."""
    import core.agents.job_reviewer
    import gateway

    gateway.Gateway.call = _refuse  # type: ignore[method-assign,assignment]
    core.agents.job_reviewer.default_gateway = _refuse  # type: ignore[assignment]


def offline_steps(
    store: "Store",
    *,
    samples_dir: Path,
    docs_dir: Path,
    truth_dir: Path,
    docs_base_url: str,
    providers_base_url: str,
    deadline_ms: int,
) -> "Steps":
    """Every step without a model: the stub Job Reviewer, Rubric Builder, Trial Builder (the
    posting's samples) and Ranker, the real Eval Builder, Candidate Finder, Trial Runner and
    Scorer, and the mock providers as the registry. The mocks must read `truth_dir`, where the
    stub Trial Builder writes the samples' answers."""
    from core.eval.eval_builder import build_eval
    from core.eval.scorer import score
    from core.pipeline.runner import Steps
    from core.runner.candidate_finder import find_candidates
    from core.runner.registry import refresh_registry
    from core.runner.trial_runner import TrialRunner
    from mocks.fake_providers import MOCKS

    return Steps(
        review=review_job_stub,
        rubric=build_rubric_stub,
        trial=SampleTrialBuilder(samples_dir, docs_dir, truth_dir),
        eval=build_eval,
        candidates=find_candidates,
        run_trial=TrialRunner(docs_base_url, deadline_ms=deadline_ms),
        score=score,
        rank=rank_stub,
        registry=functools.partial(
            refresh_registry, store, base_url=providers_base_url, slugs=list(MOCKS)
        ),
    )
