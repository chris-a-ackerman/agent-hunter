"""Agent-Hunter contracts: the only shared surface between workstreams (plan §5.1, §11.2).

Frozen Fri Oct 23, 2026. Changes need a `contract-change` PR approved by the other workstream.
"""

from contracts.base import CI95, DocId, ProviderId, new_doc_id
from contracts.events import RunEvent, RunPaused
from contracts.invoice import INVOICE_JSON_SCHEMA, Invoice, LineItem
from contracts.job import JobPosting, JobSpec, JobStatus, SampleDoc
from contracts.outcome import OutcomeReport
from contracts.provider import (
    AgentCard,
    ProviderError,
    ProviderResult,
    ProviderTask,
    RegisteredProvider,
    Submission,
)
from contracts.rubric import SCORED_FIELDS, Rubric, Tolerances
from contracts.scoring import DocScore, ScoreReport, ScoringConfig
from contracts.trial import TrialDoc, TrialPackage
from contracts.verdict import RankingRow, Verdict

# Every model with an exported JSON Schema (contracts/schemas/{Name}.json).
MODELS = (
    Invoice,
    LineItem,
    SampleDoc,
    JobPosting,
    JobSpec,
    Tolerances,
    Rubric,
    TrialDoc,
    TrialPackage,
    ScoringConfig,
    AgentCard,
    RegisteredProvider,
    ProviderTask,
    ProviderResult,
    ProviderError,
    Submission,
    DocScore,
    ScoreReport,
    RankingRow,
    Verdict,
    RunEvent,
    OutcomeReport,
)

__all__ = [
    "CI95",
    "INVOICE_JSON_SCHEMA",
    "MODELS",
    "SCORED_FIELDS",
    "AgentCard",
    "DocId",
    "DocScore",
    "Invoice",
    "JobPosting",
    "JobSpec",
    "JobStatus",
    "LineItem",
    "OutcomeReport",
    "ProviderError",
    "ProviderId",
    "ProviderResult",
    "ProviderTask",
    "RankingRow",
    "RegisteredProvider",
    "Rubric",
    "RunEvent",
    "RunPaused",
    "SampleDoc",
    "ScoreReport",
    "ScoringConfig",
    "Submission",
    "Tolerances",
    "TrialDoc",
    "TrialPackage",
    "Verdict",
    "new_doc_id",
]
