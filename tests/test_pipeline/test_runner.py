import asyncio

import pytest

from contracts import JobStatus, RegisteredProvider, RunEvent, Verdict
from core.pipeline.runner import run_pipeline
from core.store import TRANSITIONS
from mocks.fake_providers import MOCKS, card
from tests.test_pipeline.conftest import MOCK_BASE_URL, load_example

S = JobStatus
STEPS = ["review", "rubric", "trial", "eval", "candidates", "trial_runner", "score", "rank"]


def run(posting, store, steps):
    events: list[RunEvent] = []
    job_id = asyncio.run(run_pipeline(posting, store=store, steps=steps, on_event=events.append))
    return store.get_job(job_id), events


def history(record) -> list[JobStatus]:
    return [c.status for c in record.status_history]


def test_mock_job_reaches_ranked_with_a_valid_verdict(store, make_steps):
    record, events = run(load_example("invoice_clean"), store, make_steps())

    assert record.status == S.RANKED
    assert history(record) == [
        S.SUBMITTED,
        S.REVIEWING,
        S.RUBRIC_READY,
        S.TRIAL_BUILT,
        S.RUNNING,
        S.SCORED,
        S.RANKED,
    ]
    verdict = Verdict.model_validate(record.verdict.model_dump())
    assert verdict.evidence == record.trial.trial_id
    assert {r.provider for r in verdict.ranking} == {f"mock-{x}@1" for x in "abcde"}

    # Every output saved; every output carries the job's ID.
    assert record.spec.job_id == record.rubric.job_id == record.trial.job_id == record.job_id
    assert len(record.submissions) == 5 * len(record.trial.documents) == 15
    assert len(record.scores) == 5
    by_provider = {s.provider: s.status for s in record.submissions}
    assert by_provider["mock-d@1"] == "timeout"
    assert by_provider["mock-a@1"] == "ok"

    # Timings and started/finished events for all eight steps, in order.
    assert [t.step for t in record.timings] == STEPS
    assert all(t.finished_at >= t.started_at for t in record.timings)
    lifecycle = [(e.step, e.kind) for e in events if e.kind in ("started", "finished")]
    assert lifecycle == [(s, k) for s in STEPS for k in ("started", "finished")]
    assert sum(e.kind == "task_done" for e in events) == 15
    assert all(e.job_id == record.job_id for e in events)


def test_every_status_change_is_a_legal_transition(store, make_steps):
    record, _ = run(load_example("invoice_clean"), store, make_steps())
    h = history(record)
    assert all(b in TRANSITIONS[a] for a, b in zip(h, h[1:], strict=False))


def test_rejected_job_stops_at_rejected_with_the_reason(store, make_steps):
    record, events = run(load_example("reject_unscorable"), store, make_steps())

    assert record.status == S.REJECTED
    assert history(record) == [S.SUBMITTED, S.REVIEWING, S.REJECTED]
    assert record.spec.decision == "reject"
    assert record.status_history[-1].note == record.spec.reason != ""
    assert record.rubric is None and record.trial is None and record.verdict is None
    assert [t.step for t in record.timings] == ["review"]
    assert {e.step for e in events} == {"review"}


def test_needs_info_job_stops_with_the_questions_saved(store, make_steps):
    posting = load_example("invoice_clean").model_copy(update={"output_schema": None})
    record, _ = run(posting, store, make_steps())

    assert record.status == S.NEEDS_INFO
    assert history(record) == [S.SUBMITTED, S.REVIEWING, S.NEEDS_INFO]
    assert record.spec.decision == "needs_info" and record.spec.questions
    assert record.status_history[-1].note == record.spec.reason
    assert record.rubric is None


def test_run_paused_sets_paused_and_keeps_the_submissions(store, make_steps):
    record, events = run(load_example("invoice_clean"), store, make_steps(fail_budget=True))

    assert record.status == S.PAUSED
    assert history(record)[-2:] == [S.RUNNING, S.PAUSED]
    assert record.status_history[-1].note == "budget_exhausted"
    assert len(record.submissions) == 15
    assert all(s.status == "not_run" for s in record.submissions)
    assert record.scores == [] and record.verdict is None
    paused = [e for e in events if e.kind == "paused"]
    assert len(paused) == 1 and paused[0].step == "trial_runner"
    assert ("trial_runner", "finished") not in [(e.step, e.kind) for e in events]


def test_a_failing_step_emits_stopped_reraises_and_keeps_the_last_status(store, make_steps):
    def broken_rubric(spec):
        raise RuntimeError("rubric exploded")

    posting = load_example("invoice_clean")
    events: list[RunEvent] = []
    with pytest.raises(RuntimeError, match="rubric exploded"):
        asyncio.run(
            run_pipeline(
                posting,
                store=store,
                steps=make_steps(rubric=broken_rubric),
                on_event=events.append,
                job_id=(job_id := store.create_job(posting)),
            )
        )
    assert store.get_job(job_id).status == S.REVIEWING
    assert (events[-1].step, events[-1].kind) == ("rubric", "stopped")
    assert "rubric exploded" in events[-1].detail["error"]


def test_no_candidates_gives_insufficient_candidates(store, make_steps):
    record, _ = run(load_example("invoice_clean"), store, make_steps(registry=lambda: []))
    assert record.status == S.RANKED
    assert record.verdict.status == "insufficient_candidates"
    assert record.verdict.recommended is None


def test_async_registry_is_awaited(store, make_steps):
    async def registry() -> list[RegisteredProvider]:
        return [RegisteredProvider(card=card(s), base_url=MOCK_BASE_URL) for s in MOCKS]

    record, _ = run(load_example("invoice_clean"), store, make_steps(registry=registry))
    assert record.status == S.RANKED
    assert {r.provider for r in record.verdict.ranking} == {f"mock-{x}@1" for x in "abcde"}
