"""Post-hire outcome report (plan §9.3 `report_outcome`). Stub: fleshed out in the final version."""

from contracts.base import Contract, ProviderId, Score


class OutcomeReport(Contract):
    trial_id: str
    provider: ProviderId
    measured_accuracy: Score
