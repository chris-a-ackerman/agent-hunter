"""One valid instance of every contract model, for round-trip tests and as stub return values."""

from datetime import UTC, date, datetime

from contracts import (
    INVOICE_JSON_SCHEMA,
    SCORED_FIELDS,
    AgentCard,
    DocScore,
    Invoice,
    JobPosting,
    JobSpec,
    LineItem,
    OutcomeReport,
    ProviderError,
    ProviderResult,
    ProviderTask,
    RankingRow,
    RegisteredProvider,
    Rubric,
    RunEvent,
    SampleDoc,
    ScoreReport,
    ScoringConfig,
    Submission,
    Tolerances,
    TrialDoc,
    TrialPackage,
    Verdict,
)

DOC_A = "0a1b2c3d4e5f"
DOC_B = "f5e4d3c2b1a0"
WEIGHTS = dict(zip(SCORED_FIELDS, [0.30, 0.30, 0.15, 0.15, 0.05, 0.05], strict=True))
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)

LINE_ITEM = LineItem(description="Widget", quantity=2, unit_price=5.0, amount=10.0)
INVOICE = Invoice(
    vendor_name="Northwind Traders",
    invoice_number="INV-001",
    invoice_date="2026-10-01",
    currency="USD",
    line_items=[LINE_ITEM],
    subtotal=10.0,
    tax=None,
    total=10.0,
)
SAMPLE_DOC = SampleDoc(path="samples/inv1.pdf", answer=INVOICE)
JOB_POSTING = JobPosting(
    requester_id="req-1",
    description="Extract invoices to JSON",
    output_description="One JSON object per invoice",
    output_schema=INVOICE_JSON_SCHEMA,
    samples=[SAMPLE_DOC, SampleDoc(path="samples/inv2.pdf")],
    job_budget_usd=0.30,
    trial_budget_usd=0.10,
    deadline=date(2026, 11, 1),
    region="us",
)
JOB_SPEC = JobSpec(
    job_id="job-1",
    decision="approve",
    reason="Scorable invoice extraction",
    job_type="invoice_extraction",
    fields=list(SCORED_FIELDS),
    n_real_docs=15,
    per_doc_budget_usd=0.02,
    region="us",
)
TOLERANCES = Tolerances()
RUBRIC = Rubric(
    rubric_id="rub-1",
    job_id="job-1",
    weights=WEIGHTS,
    required_fields=["total", "vendor_name"],
    approved_at=NOW,
)
TRIAL_DOC = TrialDoc(doc_id=DOC_A, source="synthetic", answer=INVOICE)
TRIAL_PACKAGE = TrialPackage(
    trial_id="trial-1",
    job_id="job-1",
    documents=[TRIAL_DOC, TrialDoc(doc_id=DOC_B, source="sample", answer=INVOICE)],
    created_at=NOW,
)
SCORING_CONFIG = ScoringConfig(
    trial_id="trial-1",
    weights=WEIGHTS,
    tolerances=TOLERANCES,
    answer_keys={DOC_A: INVOICE, DOC_B: INVOICE},
    consistency_checks=["line_items_sum"],
)
AGENT_CARD = AgentCard(
    slug="acme",
    name="Acme Extract",
    description="Invoice extraction",
    version="1.0.0",
    job_types=["invoice_extraction"],
    price_per_doc_usd=0.01,
    region="global",
)
REGISTERED_PROVIDER = RegisteredProvider(card=AGENT_CARD, base_url="http://localhost:8100")
PROVIDER_TASK = ProviderTask(
    task_id="task-1",
    requester_id="req-1",
    document_url=f"http://localhost:8200/d/{DOC_A}.pdf",
    output_schema=INVOICE_JSON_SCHEMA,
    deadline_ms=60_000,
    offered_price=0.01,
)
PROVIDER_RESULT = ProviderResult(
    task_id="task-1",
    output=INVOICE.model_dump(),
    cost_usd=0.0007,
    provider_version="1.0.0",
)
PROVIDER_ERROR = ProviderError(error="budget_exhausted")
SUBMISSION = Submission(
    task_id="task-1",
    doc_id=DOC_A,
    provider="acme@1.0.0",
    status="ok",
    output=INVOICE.model_dump(),
    latency_ms=1200,
    cost_usd=0.0007,
)
DOC_SCORE = DocScore(
    doc_id=DOC_A,
    score=0.95,
    field_scores={f: 1.0 for f in SCORED_FIELDS},
    status="ok",
    sum_check_ok=True,
)
SCORE_REPORT = ScoreReport(
    provider="acme@1.0.0",
    doc_scores=[DOC_SCORE],
    trial_score=0.95,
    ci_95=(0.9, 0.99),
    n_docs=1,
)
RANKING_ROW = RankingRow(
    provider="acme@1.0.0",
    accuracy=0.95,
    ci_95=(0.9, 0.99),
    cost_per_doc=0.0007,
    timeouts=0,
    mean_latency_ms=1200.0,
)
VERDICT = Verdict(
    status="recommended",
    recommended="acme@1.0.0",
    trial_accuracy=0.95,
    ci_95=(0.9, 0.99),
    cost_per_doc=0.0007,
    valid_until=date(2026, 11, 22),
    tie=False,
    ranking=[RANKING_ROW],
    rationale="Acme led with a non-overlapping CI.",
    evidence="trial-1",
)
RUN_EVENT = RunEvent(job_id="job-1", step="trial_runner", kind="task_done", detail={"n": 1})
OUTCOME_REPORT = OutcomeReport(trial_id="trial-1", provider="acme@1.0.0", measured_accuracy=0.9)

ALL = [
    INVOICE,
    LINE_ITEM,
    SAMPLE_DOC,
    JOB_POSTING,
    JOB_SPEC,
    TOLERANCES,
    RUBRIC,
    TRIAL_DOC,
    TRIAL_PACKAGE,
    SCORING_CONFIG,
    AGENT_CARD,
    REGISTERED_PROVIDER,
    PROVIDER_TASK,
    PROVIDER_RESULT,
    PROVIDER_ERROR,
    SUBMISSION,
    DOC_SCORE,
    SCORE_REPORT,
    RANKING_ROW,
    VERDICT,
    RUN_EVENT,
    OUTCOME_REPORT,
]
