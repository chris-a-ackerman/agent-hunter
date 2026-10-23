import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from contracts import JobStatus
from core.store import (
    TRANSITIONS,
    IllegalTransition,
    JobNotFound,
    JsonStore,
    Store,
)
from gateway import JsonlCostSink, LLMCall
from tests.test_contracts.examples import (
    AGENT_CARD,
    JOB_POSTING,
    JOB_SPEC,
    REGISTERED_PROVIDER,
    RUBRIC,
    SCORE_REPORT,
    SUBMISSION,
    TRIAL_PACKAGE,
    VERDICT,
)

FIXTURES = Path(__file__).parent / "fixtures"
S = JobStatus

# Happy path through the plan §5.2 status machine.
HAPPY_PATH = [
    S.REVIEWING,
    S.RUBRIC_READY,
    S.TRIAL_BUILT,
    S.RUNNING,
    S.PAUSED,
    S.RUNNING,
    S.SCORED,
    S.RANKED,
    S.HIRED,
    S.OUTCOME_REPORTED,
]


@pytest.fixture
def store(tmp_path: Path) -> JsonStore:
    return JsonStore(root=tmp_path / "store")


def test_is_a_store(store: JsonStore) -> None:
    s: Store = store
    assert s is store


def test_create_and_get_job(store: JsonStore) -> None:
    job_id = store.create_job(JOB_POSTING)
    record = store.get_job(job_id)
    assert record.job_id == job_id
    assert record.posting == JOB_POSTING
    assert record.status == S.SUBMITTED
    assert [c.status for c in record.status_history] == [S.SUBMITTED]
    assert record.spec is record.rubric is record.trial is record.verdict is None
    assert record.submissions == record.scores == record.timings == []
    assert (store.root / "jobs" / f"{job_id}.json").is_file()


def test_job_ids_are_unique(store: JsonStore) -> None:
    assert store.create_job(JOB_POSTING) != store.create_job(JOB_POSTING)


def test_round_trip_every_save(store: JsonStore) -> None:
    job_id = store.create_job(JOB_POSTING)
    started = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
    finished = datetime(2026, 10, 23, 12, 0, 3, tzinfo=UTC)

    store.save_job_spec(job_id, JOB_SPEC)
    store.save_rubric(job_id, RUBRIC)
    store.save_trial(job_id, TRIAL_PACKAGE)
    store.save_submissions(job_id, [SUBMISSION])
    store.save_scores(job_id, [SCORE_REPORT])
    store.save_verdict(job_id, VERDICT)
    store.record_step(job_id, "job_reviewer", started, finished)

    # A fresh instance reads only what is on disk.
    record = JsonStore(root=store.root).get_job(job_id)
    assert record.spec == JOB_SPEC
    assert record.rubric == RUBRIC
    assert record.trial == TRIAL_PACKAGE
    assert record.submissions == [SUBMISSION]
    assert record.scores == [SCORE_REPORT]
    assert record.verdict == VERDICT
    assert len(record.timings) == 1
    timing = record.timings[0]
    assert (timing.step, timing.started_at, timing.finished_at) == (
        "job_reviewer",
        started,
        finished,
    )


def test_saves_replace_and_steps_append(store: JsonStore) -> None:
    job_id = store.create_job(JOB_POSTING)
    store.save_submissions(job_id, [SUBMISSION, SUBMISSION])
    store.save_submissions(job_id, [SUBMISSION])
    t = datetime(2026, 10, 23, tzinfo=UTC)
    store.record_step(job_id, "a", t, t)
    store.record_step(job_id, "b", t, t)
    record = store.get_job(job_id)
    assert record.submissions == [SUBMISSION]
    assert [s.step for s in record.timings] == ["a", "b"]


def test_happy_path_statuses(store: JsonStore) -> None:
    job_id = store.create_job(JOB_POSTING)
    for status in HAPPY_PATH:
        store.set_status(job_id, status, note=f"-> {status}")
    record = store.get_job(job_id)
    assert record.status == S.OUTCOME_REPORTED
    assert [c.status for c in record.status_history] == [S.SUBMITTED, *HAPPY_PATH]
    assert record.status_history[-1].note == "-> outcome_reported"


def test_needs_info_goes_back_to_review(store: JsonStore) -> None:
    job_id = store.create_job(JOB_POSTING)
    for status in (S.REVIEWING, S.NEEDS_INFO, S.REVIEWING, S.REJECTED):
        store.set_status(job_id, status)
    assert store.get_job(job_id).status == S.REJECTED


@pytest.mark.parametrize(
    ("path", "illegal"),
    [
        ([], S.RUNNING),  # skipping steps
        ([], S.SUBMITTED),  # same status
        ([S.REVIEWING, S.REJECTED], S.REVIEWING),  # out of a final status
        ([S.REVIEWING, S.RUBRIC_READY, S.TRIAL_BUILT], S.PAUSED),  # paused only from running
        ([S.REVIEWING, S.RUBRIC_READY], S.REVIEWING),  # backwards
    ],
)
def test_illegal_transition_raises(
    store: JsonStore, path: list[JobStatus], illegal: JobStatus
) -> None:
    job_id = store.create_job(JOB_POSTING)
    for status in path:
        store.set_status(job_id, status)
    before = store.get_job(job_id)
    with pytest.raises(IllegalTransition) as exc:
        store.set_status(job_id, illegal)
    assert exc.value.target == illegal
    assert store.get_job(job_id) == before  # nothing written


def test_every_status_is_reachable() -> None:
    reachable = {S.SUBMITTED}
    frontier = [S.SUBMITTED]
    while frontier:
        for nxt in TRANSITIONS.get(frontier.pop(), frozenset()):
            if nxt not in reachable:
                reachable.add(nxt)
                frontier.append(nxt)
    assert reachable == set(JobStatus)


@pytest.mark.parametrize("job_id", ["job-missing", "../providers", "a/b", ""])
def test_unknown_job_raises(store: JsonStore, job_id: str) -> None:
    with pytest.raises(JobNotFound):
        store.get_job(job_id)
    with pytest.raises(JobNotFound):
        store.save_verdict(job_id, VERDICT)


def test_providers_upsert_and_list(store: JsonStore) -> None:
    assert store.list_providers() == []
    store.upsert_provider(REGISTERED_PROVIDER)
    moved = REGISTERED_PROVIDER.model_copy(update={"base_url": "http://localhost:9999"})
    store.upsert_provider(moved)  # same provider ID: replaced
    v2 = REGISTERED_PROVIDER.model_copy(
        update={"card": AGENT_CARD.model_copy(update={"version": "2.0.0"})}
    )
    store.upsert_provider(v2)  # new version: added
    providers = JsonStore(root=store.root).list_providers()
    assert [p.provider_id for p in providers] == ["acme@1.0.0", "acme@2.0.0"]
    assert providers[0].base_url == "http://localhost:9999"


def test_platform_spend_sums_fixture_jsonl(store: JsonStore) -> None:
    store.root.mkdir(parents=True)
    shutil.copy(FIXTURES / "llm_calls.jsonl", store.llm_calls_path)
    assert store.platform_spend("job-aaa") == pytest.approx(0.0043)
    assert store.platform_spend("job-bbb") == pytest.approx(0.0050)
    assert store.platform_spend("job-none") == 0.0


def test_platform_spend_without_log(store: JsonStore) -> None:
    assert store.platform_spend("job-aaa") == 0.0


def test_platform_spend_reads_gateway_sink(store: JsonStore) -> None:
    sink = JsonlCostSink(store.llm_calls_path)
    for cost in (0.001, 0.002):
        sink.record(LLMCall(account="x", component="c", model="m", cost_usd=cost, job_id="j1"))
    assert store.platform_spend("j1") == pytest.approx(0.003)


def test_root_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORE_DIR", str(tmp_path / "env-store"))
    assert JsonStore().root == tmp_path / "env-store"
    monkeypatch.delenv("STORE_DIR")
    assert JsonStore().root == Path(".data/store")


def test_no_temp_files_left(store: JsonStore) -> None:
    job_id = store.create_job(JOB_POSTING)
    store.save_verdict(job_id, VERDICT)
    store.upsert_provider(REGISTERED_PROVIDER)
    leftovers = [p for p in store.root.rglob("*") if p.name.endswith(".tmp")]
    assert leftovers == []
