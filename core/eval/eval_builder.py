"""The Eval Builder (AGE-23, plan §3 #4): binds the Rubric to the trial's answer key.

`build_eval` satisfies `contracts.steps.BuildEval`. Plain code: no LLM, no I/O.
"""

from collections import Counter

from contracts import Invoice, Rubric, ScoringConfig, TrialPackage
from contracts.rubric import SCORED_FIELDS

_WEIGHT_SUM_TOL = 1e-6


class EvalBuildError(ValueError):
    """The rubric and trial can't be turned into a ScoringConfig. Lists every problem found."""


def _problems(rubric: Rubric, trial: TrialPackage) -> list[str]:
    problems = []
    # Weights are already checked by the Rubric contract; checked again here so a rubric built
    # without validation (e.g. model_construct) fails with a clear error instead of a
    # ValidationError deep inside ScoringConfig.
    unknown = sorted(set(rubric.weights) - set(SCORED_FIELDS))
    if unknown:
        problems.append(f"weighted fields that aren't scored: {unknown}")
    total = sum(rubric.weights.values())
    if abs(total - 1.0) > _WEIGHT_SUM_TOL:
        problems.append(f"weights sum to {total}, not 1 (±{_WEIGHT_SUM_TOL})")

    if rubric.job_id != trial.job_id:
        problems.append(f"rubric is for job {rubric.job_id!r}, trial for job {trial.job_id!r}")
    if not trial.documents:
        problems.append("the trial has no documents")

    counts = Counter(d.doc_id for d in trial.documents)
    dupes = sorted(i for i, n in counts.items() if n > 1)
    if dupes:
        problems.append(f"duplicate doc IDs: {dupes}")

    not_fields = sorted(set(rubric.required_fields) - set(Invoice.model_fields))
    if not_fields:
        problems.append(f"required fields that aren't Invoice fields: {not_fields}")
    for f in rubric.required_fields:
        if f in not_fields:
            continue
        missing = [d.doc_id for d in trial.documents if getattr(d.answer, f) is None]
        if missing:
            problems.append(f"required field {f!r} is null in the answer key for {missing}")
    return problems


def build_eval(rubric: Rubric, trial: TrialPackage) -> ScoringConfig:
    """Raises EvalBuildError if the pair is inconsistent (see _problems)."""
    problems = _problems(rubric, trial)
    if problems:
        raise EvalBuildError("; ".join(problems))
    return ScoringConfig(
        trial_id=trial.trial_id,
        weights=dict(rubric.weights),
        tolerances=rubric.tolerances,
        answer_keys={d.doc_id: d.answer for d in trial.documents},
        consistency_checks=list(rubric.consistency_checks),
    )
