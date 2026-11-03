"""The HW7 benchmark harness (AGE-36) end to end on the mock providers: no model calls.

The jobs are j01 (its committed posting and samples) and a stand-in "j02" with `source: docile`
built from the same posting, so the test needs neither DocILE nor rendered PDFs."""

import asyncio
import json
import shutil
import socket
from pathlib import Path
from typing import Any

import pytest

from contracts import AgentCard, RegisteredProvider
from core.runner import trial_runner
from experiments.hw7 import run_benchmark as rb
from experiments.hw7.make_jobs import JOBS_DIR, SYNTH_JOBS, build_synth_heldout
from experiments.hw7.run_benchmark import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_SPEND_CAP,
    HarnessError,
    MockGateway,
    Options,
    Paths,
    run_benchmark,
)

MOCKS = ["mock-a", "mock-b", "mock-c", "mock-d", "mock-e"]
N_HELDOUT = N_TRIAL = 12


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def bench(tmp_path, monkeypatch) -> Paths:
    """A jobs dir with j01 and a DocILE-like j02, their held-out answer keys, free ports, and
    a Trial Runner without pacing delays (the 0–1.5 s waits only slow the test down)."""
    jobs = tmp_path / "jobs"
    manifest = {e["job"]: e for e in json.loads((JOBS_DIR / "manifest.json").read_text())["jobs"]}
    shutil.copytree(JOBS_DIR / "samples" / "j01", jobs / "samples" / "j01")
    posting = json.loads((JOBS_DIR / "j01.json").read_text())
    (jobs / "j01.json").write_text(json.dumps(posting))
    shutil.copytree(JOBS_DIR / "samples" / "j01", jobs / "samples" / "j02")
    for s in posting["samples"]:
        s["path"] = s["path"].replace("j01", "j02")
    (jobs / "j02.json").write_text(json.dumps(posting))

    dirs = {"docs_dir": tmp_path / "docs", "truth_dir": tmp_path / "truth"}
    j01 = dict(manifest["j01"])
    assert build_synth_heldout(SYNTH_JOBS["j01"], render=False, **dirs) == j01["heldout_doc_ids"]
    j02 = dict(manifest["j02"])
    j02["heldout_doc_ids"] = build_synth_heldout(SYNTH_JOBS["j03"], render=False, **dirs)
    (jobs / "manifest.json").write_text(json.dumps({"jobs": [j01, j02]}))

    env = {
        "STORE_DIR": str(tmp_path / "store"),
        "DOCS_DIR": str(dirs["docs_dir"]),
        "TRUTH_DIR": str(dirs["truth_dir"]),
        "DOCS_BASE_URL": f"http://127.0.0.1:{free_port()}",
        "MOCK_PROVIDERS_BASE_URL": f"http://127.0.0.1:{free_port()}",
        "MOCK_PROVIDER_SLUGS": ",".join(MOCKS),
        "MOCK_TASK_DEADLINE_MS": "300",
        "MAX_CONCURRENCY": "16",
        "SCORER": "mock",
        "PROVIDERS_BASE_URL": "http://unused",
        "PROVIDER_SLUGS": "unused",
        "TASK_DEADLINE_MS": "60000",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    no_pacing = trial_runner.TrialRunner.from_env.__func__  # type: ignore[attr-defined]

    def from_env(cls):
        runner = no_pacing(cls)
        runner.max_delay_s = 0
        return runner

    monkeypatch.setattr(trial_runner.TrialRunner, "from_env", classmethod(from_env))
    return Paths(
        jobs_dir=jobs,
        rubrics_dir=tmp_path / "rubrics",
        runs_dir=tmp_path / "runs",
        **dirs,
    )


def run(paths: Paths, gateway=None, **opts: Any) -> tuple[int, Path]:
    merged: dict[str, Any] = {"mock": True, "only": ["j01", "j02"], "repeat": 2, "run_id": "t1"}
    return asyncio.run(run_benchmark(Options(**(merged | opts)), paths=paths, gateway=gateway))


def rows(run_dir: Path, name: str) -> list[dict]:
    return [json.loads(line) for line in (run_dir / name).read_text().splitlines() if line]


def test_mock_run_logs_every_method_on_every_job(bench):
    code, run_dir = run(bench)
    assert code == EXIT_OK

    subs = rows(run_dir, "submissions.jsonl")
    per_job = len(MOCKS) * (2 * N_TRIAL + N_HELDOUT)
    assert len(subs) == 2 * per_job
    assert len({(r["job"], r["repeat"], r["provider"], r["doc_id"]) for r in subs}) == len(subs)
    for r in subs:
        assert r["run_id"] == "t1" and r["git_commit"]
        assert r["arm"] == ("heldout" if r["repeat"] == "heldout" else "trial")
        assert r["job_source"] == {"j01": "synthetic", "j02": "docile"}[r["job"]]
        assert set(r["field_scores"]) >= {"total", "vendor_name", "line_items"}
        assert 0.0 <= r["score"] <= 1.0
        assert r["finished_at"]
    trial = [r for r in subs if r["arm"] == "trial"]
    sources = {(r["job"], r["repeat"], r["provider"]): [] for r in trial}
    for r in trial:
        sources[(r["job"], r["repeat"], r["provider"])].append(r["doc_source"])
    for s in sources.values():  # 3 requester samples + 9 synthetic per trial
        assert sorted(s) == ["sample"] * 3 + ["synthetic"] * 9
    assert {r["doc_source"] for r in subs if r["arm"] == "heldout"} == {"heldout"}
    # The two repeats are different trials.
    docs = {r["repeat"]: set() for r in trial if r["job"] == "j01"}
    for r in trial:
        if r["job"] == "j01":
            docs[r["repeat"]].add(r["doc_id"])
    assert docs[1].isdisjoint(docs[2])

    # The mock providers' known behaviour: mock-d times out, mock-e is invalid, mock-a is best.
    held = [r for r in subs if r["arm"] == "heldout" and r["job"] == "j01"]
    mean = {
        p: sum(r["score"] for r in held if r["provider"].startswith(p)) / N_HELDOUT for p in MOCKS
    }
    assert max(mean, key=mean.get) == "mock-a"
    assert {r["status"] for r in held if r["provider"] == "mock-d@1"} == {"timeout"}
    assert {r["doc_status"] for r in held if r["provider"] == "mock-e@1"} == {"invalid"}

    picks = rows(run_dir, "picks.jsonl")
    assert len(picks) == 2 * (2 + 5 + 1)
    tth = [p for p in picks if p["method"] == "tth"]
    assert sorted((p["job"], p["run"]) for p in tth) == [
        ("j01", 1),
        ("j01", 2),
        ("j02", 1),
        ("j02", 2),
    ]
    for p in tth:
        assert p["picked"] in {"mock-a@1", "mock-b@1"}
        assert p["verdict_status"] in {"recommended", "tie"}
        assert p["selection_time_ms"] > 0 and p["selection_cost_usd"] > 0
        assert set(p["steps_ms"]) >= {"review", "rubric", "trial", "trial_runner", "rank"}
    assert {p["picked"] for p in picks if p["method"] == "profile"} == {"mock-a@1"}
    assert {p["picked"] for p in picks if p["method"] == "cheapest"} == {"mock-c@1"}

    trial_file = json.loads((run_dir / "trials" / "j01-r1.json").read_text())
    assert len(trial_file["documents"]) == N_TRIAL
    assert all(set(d) == {"doc_id", "source"} for d in trial_file["documents"])  # no answers

    # Mock rubrics stay in the run, not in the committed rubrics dir.
    assert (run_dir / "rubrics" / "j01.json").is_file()
    assert not bench.rubrics_dir.exists()

    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["stopped"] is None and summary["errors"] == []
    assert summary["jobs_completed"] == ["j01", "j02"]
    assert summary["providers_commit"] == "5d9536a"
    assert summary["settings"]["trial_size"] == 12
    assert summary["settings"]["max_concurrency"] == 16
    spend = summary["spend"]
    assert spend["provider_calls"] == len(subs)
    assert spend["provider_usd"] == pytest.approx(sum(r["cost_usd"] or 0 for r in subs))
    assert spend["platform_usd"] == 0.0 and spend["platform_calls"] > 0
    assert summary["failures_by_job_type"]["clean"]["timeout"] == 2 * per_job // len(MOCKS)


def test_rerun_with_the_same_run_id_resumes_without_resending(bench):
    code, run_dir = run(bench)
    assert code == EXIT_OK
    subs = (run_dir / "submissions.jsonl").read_text().splitlines()
    picks = (run_dir / "picks.jsonl").read_text().splitlines()

    # Fully done: nothing is sent or picked again, and no model is called.
    calls = len(rows(run_dir, "llm_calls.jsonl"))
    assert run(bench) == (EXIT_OK, run_dir)
    assert (run_dir / "submissions.jsonl").read_text().splitlines() == subs
    assert len(rows(run_dir, "llm_calls.jsonl")) == calls

    # A crash in j02's second trial: its held-out batch (60 rows), 40 of its trial rows and
    # every pick from that trial on (r2, 5 profile, cheapest) are lost. The rerun fills them in.
    (run_dir / "submissions.jsonl").write_text("\n".join(subs[:-100]) + "\n")
    (run_dir / "picks.jsonl").write_text("\n".join(picks[:-7]) + "\n")
    assert run(bench)[0] == EXIT_OK
    after = rows(run_dir, "submissions.jsonl")
    assert len(after) == len(subs)
    assert len({(r["job"], r["repeat"], r["provider"], r["doc_id"]) for r in after}) == len(subs)
    assert len(rows(run_dir, "picks.jsonl")) == len(picks)


def test_heldout_answers_are_reused_by_a_new_run(bench):
    run(bench, only=["j01"], repeat=1, run_id="first")
    code, run_dir = run(bench, only=["j01"], repeat=1, run_id="second")
    assert code == EXIT_OK
    held = [r for r in rows(run_dir, "submissions.jsonl") if r["arm"] == "heldout"]
    assert len(held) == len(MOCKS) * N_HELDOUT
    assert {r["cached_from"] for r in held} == {"first"}
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["spend"]["provider_rows_reused"] == len(held)
    assert summary["spend"]["provider_calls"] == len(MOCKS) * N_TRIAL


def test_spend_cap_stops_before_new_work(bench):
    code, run_dir = run(bench, max_spend_usd=0.01)
    assert code == EXIT_SPEND_CAP
    summary = json.loads((run_dir / "summary.json").read_text())
    assert summary["stopped"] == "spend_cap"
    assert summary["jobs_completed"] == []
    # One trial batch (mock-a..e × 12 ≈ $0.078) went over the cap; nothing started after it.
    assert len(rows(run_dir, "submissions.jsonl")) == len(MOCKS) * N_TRIAL


def test_a_job_the_reviewer_does_not_approve_is_an_error(bench):
    gw = MockGateway("mock-a")
    gw.replies["job_reviewer"] = {"decision": "reject", "reason": "Not invoices."}
    code, run_dir = run(bench, only=["j01"], gateway=gw)
    assert code == EXIT_ERROR
    summary = json.loads((run_dir / "summary.json").read_text())
    assert "j01: the Job Reviewer said 'reject'" in summary["errors"][0]
    assert not (run_dir / "submissions.jsonl").exists()


def test_missing_docile_inputs_stop_with_a_clear_error(bench):
    (bench.jobs_dir / "j02.json").unlink()
    with pytest.raises(HarnessError, match="j02: posting .* missing; download DocILE"):
        run(bench)


def test_unknown_job_is_rejected(bench):
    with pytest.raises(HarnessError, match="unknown jobs in --only: j99"):
        run(bench, only=["j99"])


def test_real_run_refuses_a_platform_account_other_than_nathalies(bench, monkeypatch):
    import core.agents.job_reviewer as jr
    import core.runner.registry as reg

    class ChrisGateway:
        account = "chris"

    card = AgentCard(
        slug="mock-a",
        name="A",
        description="",
        version="1",
        job_types=["invoice_extraction"],
        price_per_doc_usd=0.01,
        region="global",
    )

    async def registry():
        return [RegisteredProvider(card=card, base_url="http://unused")]

    monkeypatch.setattr(reg, "refresh_registry", registry)
    monkeypatch.setattr(jr, "default_gateway", lambda: ChrisGateway())
    monkeypatch.setattr(rb, "preflight", lambda *a, **k: None)
    with pytest.raises(HarnessError, match="PARLEY_ACCOUNT='chris', not 'nathalie'"):
        run(bench, mock=False)
