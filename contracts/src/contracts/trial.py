"""The Trial Builder's output. The answer key never leaves the core."""

from datetime import datetime
from typing import Literal

from contracts.base import Contract, DocId
from contracts.invoice import Invoice


class TrialDoc(Contract):
    doc_id: DocId
    source: Literal["sample", "synthetic", "trial_real"]
    answer: Invoice


class TrialPackage(Contract):
    trial_id: str
    job_id: str
    documents: list[TrialDoc]
    created_at: datetime
