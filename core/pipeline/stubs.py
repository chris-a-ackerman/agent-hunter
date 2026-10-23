"""Stand-ins for the steps that don't exist yet, so the pipeline runs end to end today.

Each one satisfies its `contracts.steps` Protocol and is deliberately simple. `config.build_steps`
swaps each for the real step once it lands:

    review_job_stub       → Job Reviewer (AGE-13)
    build_rubric_stub     → Rubric Builder (AGE-21)
    SampleTrialBuilder    → Trial Builder (AGE-22)
    find_candidates_stub  → Candidate Finder (AGE-14)
    HttpTrialRunner       → Trial Runner (AGE-15)
    rank_stub             → Ranker (AGE-24)
"""

import asyncio
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean

import httpx

from contracts import (
    INVOICE_JSON_SCHEMA,
    JobPosting,
    JobSpec,
    ProviderError,
    ProviderResult,
    ProviderTask,
    RankingRow,
    RegisteredProvider,
    Rubric,
    RunEvent,
    RunPaused,
    ScoreReport,
    Submission,
    TrialDoc,
    TrialPackage,
    Verdict,
    new_doc_id,
)
from contracts.rubric import SCORED_FIELDS
from contracts.steps import OnEvent
from core.eval.defaults import DEFAULT_WEIGHTS
from core.eval.stats import decide

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


# --- Candidate Finder -------------------------------------------------------------------------


def find_candidates_stub(
    spec: JobSpec, registry: list[RegisteredProvider]
) -> list[RegisteredProvider]:
    """Declared job type, region ("global" matches any) and list price within the per-doc
    budget (D19)."""
    return [
        p
        for p in registry
        if (spec.job_type or JOB_TYPE) in p.card.job_types
        and (spec.region is None or p.card.region in ("global", spec.region))
        and p.card.price_per_doc_usd <= spec.per_doc_budget_usd
    ]


# --- Trial Runner -----------------------------------------------------------------------------


class HttpTrialRunner:
    """Sends every (document, provider) task over HTTP (plan §9.2) under a concurrency cap.

    Handles the D18 responses: 200 + ProviderResult → ok; 402 budget_exhausted → stop sending,
    mark unsent tasks not_run and raise RunPaused; no answer within the deadline → timeout;
    anything else → error. No pacing delay yet (D23 is AGE-15's).
    """

    def __init__(
        self,
        docs_base_url: str,
        *,
        deadline_ms: int = 60_000,
        concurrency: int = 8,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.docs_base_url = docs_base_url.rstrip("/")
        self.deadline_ms, self.concurrency, self.transport = deadline_ms, concurrency, transport

    async def __call__(
        self,
        trial: TrialPackage,
        providers: list[RegisteredProvider],
        requester_id: str,
        on_event: OnEvent | None = None,
    ) -> list[Submission]:
        sem = asyncio.Semaphore(self.concurrency)
        paused = asyncio.Event()

        async def one(client: httpx.AsyncClient, doc: TrialDoc, p: RegisteredProvider):
            async with sem:
                if paused.is_set():
                    return _not_run(doc, p)
                sub = await self._send(client, doc, p, requester_id, paused)
            if on_event is not None:
                on_event(
                    RunEvent(
                        job_id=trial.job_id,
                        step="trial_runner",
                        kind="task_done",
                        detail={
                            "provider": sub.provider,
                            "doc_id": sub.doc_id,
                            "status": sub.status,
                        },
                    )
                )
            return sub

        async with httpx.AsyncClient(transport=self.transport) as client:
            subs = await asyncio.gather(
                *(one(client, d, p) for p in providers for d in trial.documents)
            )
        if paused.is_set():
            raise RunPaused(list(subs))
        return list(subs)

    async def _send(
        self,
        client: httpx.AsyncClient,
        doc: TrialDoc,
        p: RegisteredProvider,
        requester_id: str,
        paused: asyncio.Event,
    ) -> Submission:
        task = ProviderTask(
            task_id=uuid.uuid4().hex,
            requester_id=requester_id,
            document_url=f"{self.docs_base_url}/d/{doc.doc_id}.pdf",
            output_schema=INVOICE_JSON_SCHEMA,
            deadline_ms=self.deadline_ms,
            offered_price=p.card.price_per_doc_usd,
        )
        base = {"task_id": task.task_id, "doc_id": doc.doc_id, "provider": p.provider_id}
        t0 = time.monotonic()
        try:
            resp = await asyncio.wait_for(
                client.post(p.tasks_url, json=task.model_dump(), timeout=None),
                timeout=self.deadline_ms / 1000,
            )
        except TimeoutError:
            return Submission(
                **base, status="timeout", output=None, latency_ms=self.deadline_ms, cost_usd=None
            )
        except httpx.HTTPError:
            return Submission(
                **base, status="error", output=None, latency_ms=_ms(t0), cost_usd=None
            )
        latency = _ms(t0)
        if resp.status_code == 402 and _error_code(resp) == "budget_exhausted":
            paused.set()
            return _not_run(doc, p, task.task_id)
        if resp.status_code == 200:
            try:
                result = ProviderResult.model_validate_json(resp.content)
            except ValueError:
                result = None
            if result is not None:
                return Submission(
                    **base,
                    status="ok",
                    output=result.output,
                    latency_ms=latency,
                    cost_usd=result.cost_usd,
                )
        return Submission(**base, status="error", output=None, latency_ms=latency, cost_usd=None)


def _ms(t0: float) -> int:
    return round((time.monotonic() - t0) * 1000)


def _error_code(resp: httpx.Response) -> str | None:
    try:
        return ProviderError.model_validate_json(resp.content).error
    except ValueError:
        return None


def _not_run(doc: TrialDoc, p: RegisteredProvider, task_id: str | None = None) -> Submission:
    return Submission(
        task_id=task_id or uuid.uuid4().hex,
        doc_id=doc.doc_id,
        provider=p.provider_id,
        status="not_run",
        output=None,
        latency_ms=None,
        cost_usd=None,
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
