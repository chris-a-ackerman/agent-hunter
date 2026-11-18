"""The profile-variant runner (AGE-63) on the mock gateway: no model calls, no network.

Run folders go to a temp dir. The jobs dir is a temp copy of the manifest and postings; the
DocILE postings (j02, j09) aren't in git, so they get j01's posting as a stand-in (the profile
baseline only reads the posting's text and budget)."""

import functools
import json
import socket
from pathlib import Path
from typing import Any

import pytest

from experiments.hw7 import profile_variants as pv
from experiments.hw7.run_benchmark import EXIT_OK, EXIT_SPEND_CAP, JOBS_DIR, HarnessError
from gateway import GatewayResponse

N_JOBS = 12
# the keys of a run_benchmark profile-pick row (experiments/hw7/runs/*/picks.jsonl)
PROFILE_ROW_KEYS = {
    "run_id",
    "git_commit",
    "job",
    "job_source",
    "job_type",
    "method",
    "run",
    "picked",
    "verdict_status",
    "tied",
    "reason",
    "attempts",
    "failed",
    "selection_cost_usd",
    "selection_time_ms",
    "tokens_in",
    "tokens_out",
    "finished_at",
}


def write_jobs(tmp_path: Path) -> Path:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    manifest = json.loads((JOBS_DIR / "manifest.json").read_text(encoding="utf-8"))
    (jobs / "manifest.json").write_text(json.dumps(manifest))
    stand_in = (JOBS_DIR / "j01.json").read_text(encoding="utf-8")
    for e in manifest["jobs"]:
        src = JOBS_DIR / e["posting"]
        (jobs / e["posting"]).write_text(
            src.read_text(encoding="utf-8") if src.is_file() else stand_in
        )
    return jobs


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("network access in an offline test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_mock_inflated_logs_60_picks_offline_and_resumes(tmp_path, no_network):
    jobs, runs = write_jobs(tmp_path), tmp_path / "runs"
    opts = pv.VariantOptions(variant="inflated", run_id="test-mock", mock=True)
    code, run_dir = pv.run_variant(opts, jobs_dir=jobs, runs_dir=runs)
    assert code == EXIT_OK
    assert run_dir == runs / "test-mock"

    picks = _jsonl(run_dir / "picks.jsonl")
    assert len(picks) == N_JOBS * 5
    assert {p["method"] for p in picks} == {"profile:inflated"}
    assert all(set(p) == PROFILE_ROW_KEYS for p in picks)
    assert {(p["job"], p["run"]) for p in picks} == {
        (e["job"], k)
        for e in json.loads((jobs / "manifest.json").read_text())["jobs"]
        for k in range(1, 6)
    }
    assert {p["picked"] for p in picks} == {"docuvant@0.1.0"}  # the mock answers the first card
    assert not any(p["failed"] for p in picks)

    calls = _jsonl(run_dir / "llm_calls.jsonl")
    assert len(calls) == N_JOBS * 5
    assert {c["account"] for c in calls} == {"mock"}
    assert {c["component"] for c in calls} == {"baseline_profile"}
    assert "hw7-j01-vinflated-p1" in {c["job_id"] for c in calls}
    assert sum(c["cost_usd"] for c in calls) == 0

    code, _ = pv.run_variant(opts, jobs_dir=jobs, runs_dir=runs)
    assert code == EXIT_OK
    assert len(_jsonl(run_dir / "picks.jsonl")) == N_JOBS * 5
    assert len(_jsonl(run_dir / "llm_calls.jsonl")) == N_JOBS * 5


def test_variants_share_a_run(tmp_path, no_network):
    jobs, runs = write_jobs(tmp_path), tmp_path / "runs"
    for variant in pv.VARIANTS:
        opts = pv.VariantOptions(variant=variant, jobs="fresh", n=2, run_id="v", mock=True)
        assert pv.run_variant(opts, jobs_dir=jobs, runs_dir=runs)[0] == EXIT_OK
    picks = _jsonl(runs / "v" / "picks.jsonl")
    assert {p["method"] for p in picks} == {f"profile:{v}" for v in pv.VARIANTS}
    assert len(picks) == 3 * 4 * 2  # variants × fresh jobs × n


def test_the_variants_cards_reach_the_model(tmp_path):
    """The prompt carries the variant's card text, e.g. the inflated description everywhere."""
    from experiments.hw7.analyze import load_cards

    seen: list[str] = []

    class Spy:
        account = "mock"

        def call(self, component, model, messages, **kw):
            seen.append(messages[-1]["content"])
            return GatewayResponse('{"slug": "veridex"}', {"slug": "veridex"}, 0.0, 1, 1)

    jobs, runs = write_jobs(tmp_path), tmp_path / "runs"
    opts = pv.VariantOptions(variant="inflated", jobs="fresh", n=1, run_id="spy")
    assert pv.run_variant(opts, jobs_dir=jobs, runs_dir=runs, gateway=Spy())[0] == EXIT_OK
    description = load_cards(pv.CARDS_ROOT / "inflated")[0].description
    assert len(seen) == 4 and all(s.count(description) == 7 for s in seen)
    assert {p["picked"] for p in _jsonl(runs / "spy" / "picks.jsonl")} == {"veridex@0.1.0"}


class PricedGateway:
    """Answers a valid slug; every call costs `cost` USD."""

    account = "mock"

    def __init__(self, cost: float) -> None:
        self.cost, self.n = cost, 0

    def call(self, component, model, messages, **kw):
        self.n += 1
        return GatewayResponse('{"slug": "veridex"}', {"slug": "veridex"}, self.cost, 10, 10)


def test_spend_cap_stops_before_the_next_call(tmp_path):
    jobs, runs = write_jobs(tmp_path), tmp_path / "runs"
    gw = PricedGateway(cost=0.4)
    opts = pv.VariantOptions(variant="original", run_id="cap", max_spend_usd=1.0)
    code, run_dir = pv.run_variant(opts, jobs_dir=jobs, runs_dir=runs, gateway=gw)
    assert code == EXIT_SPEND_CAP
    # $0, $0.40, $0.80 are under the cap; at $1.20 the next pick doesn't start
    assert gw.n == 3
    assert len(_jsonl(run_dir / "picks.jsonl")) == 3
    assert len(_jsonl(run_dir / "llm_calls.jsonl")) == 3

    code, _ = pv.run_variant(opts, jobs_dir=jobs, runs_dir=runs, gateway=gw)
    assert code == EXIT_SPEND_CAP and gw.n == 3  # a rerun counts the logged spend


def test_missing_posting_is_a_clear_error(tmp_path):
    jobs = write_jobs(tmp_path)
    (jobs / "j05.json").unlink()
    opts = pv.VariantOptions(variant="original", run_id="x", mock=True)
    with pytest.raises(HarnessError, match="j05"):
        pv.run_variant(opts, jobs_dir=jobs, runs_dir=tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


def test_live_run_checks_the_platform_account(tmp_path, monkeypatch):
    from core.agents import job_reviewer

    class Other:
        account = "someone-else"

    monkeypatch.setattr(job_reviewer, "default_gateway", lambda: Other())
    opts = pv.VariantOptions(variant="original", run_id="x")
    with pytest.raises(HarnessError, match="PARLEY_ACCOUNT"):
        pv.run_variant(opts, jobs_dir=write_jobs(tmp_path), runs_dir=tmp_path / "runs")


def test_unknown_variant_is_a_clear_error(tmp_path):
    with pytest.raises(HarnessError, match="unknown variant"):
        pv.run_variant(pv.VariantOptions(variant="nope", mock=True), runs_dir=tmp_path)


def test_cli_mock_run(tmp_path, monkeypatch, no_network, capsys):
    jobs, runs = write_jobs(tmp_path), tmp_path / "runs"
    monkeypatch.setattr(
        pv, "run_variant", functools.partial(pv.run_variant, jobs_dir=jobs, runs_dir=runs)
    )
    argv = ["--variant", "price_shuffled", "--mock", "--run-id", "test-mock", "--n", "1"]
    assert pv.main(argv) == EXIT_OK
    assert "12 profile:price_shuffled picks" in capsys.readouterr().out
    assert len(_jsonl(runs / "test-mock" / "picks.jsonl")) == N_JOBS
