"""run_job (AGE-17): snapshots of the final views, and end-to-end `--mock` runs with a fake
Job Reviewer gateway (no model calls)."""

import asyncio
import os
import socket
from pathlib import Path

import pytest
from rich.console import Console

from contracts import JobSpec, RankingRow, Submission, Verdict
from core.run_job import rejected_view, run, summary_line, verdict_view
from core.store import JsonStore
from examples.make_examples import EXAMPLES_DIR
from mocks.fake_gateway import FakeGateway

SNAPSHOTS = Path(__file__).parent / "snapshots"
APPROVE = {"decision": "approve", "reason": "Invoice extraction to JSON.", "questions": []}
REJECT = {"decision": "reject", "reason": "Cover letters have no single right answer."}


def render(renderable) -> str:
    console = Console(record=True, width=90, color_system=None, force_terminal=False)
    console.print(renderable)
    return console.export_text()


def assert_snapshot(name: str, text: str) -> None:
    """Compare with tests/snapshots/{name}.txt; UPDATE_SNAPSHOTS=1 rewrites it."""
    path = SNAPSHOTS / f"{name}.txt"
    if os.environ.get("UPDATE_SNAPSHOTS"):
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert text == path.read_text(encoding="utf-8")


def row(provider: str, acc: float, ci: tuple[float, float], cost: float, timeouts: int = 0):
    return RankingRow(
        provider=provider,
        accuracy=acc,
        ci_95=ci,
        cost_per_doc=cost,
        timeouts=timeouts,
        mean_latency_ms=900.0,
    )


RANKING = [
    row("alpha@1", 0.931, (0.902, 0.955), 0.004),
    row("bravo@2", 0.812, (0.77, 0.85), 0.001),
    row("charlie@1", 0.0, (0.0, 0.0), 0.0, timeouts=3),
]


def spec(decision: str, reason: str) -> JobSpec:
    return JobSpec(
        job_id="job-1", decision=decision, reason=reason, n_real_docs=15, per_doc_budget_usd=0.33
    )


def test_ranking_snapshot():
    verdict = Verdict(
        status="recommended",
        recommended="alpha@1",
        trial_accuracy=0.931,
        ci_95=(0.902, 0.955),
        cost_per_doc=0.004,
        valid_until=None,
        tie=False,
        ranking=RANKING,
        rationale="alpha@1 leads and its confidence interval clears the runner-up's.",
        evidence="trial-1",
    )
    assert_snapshot("run_job_ranking", render(verdict_view(verdict)))


def test_tie_marks_tied_rows_and_says_broken_on_cost():
    verdict = Verdict(
        status="tie",
        recommended="bravo@2",
        tied=["alpha@1", "bravo@2"],
        trial_accuracy=0.812,
        ci_95=(0.77, 0.85),
        cost_per_doc=0.001,
        valid_until=None,
        tie=True,
        ranking=RANKING,
        rationale="2 providers overlap; bravo@2 wins on cost.",
        evidence="trial-1",
    )
    text = render(verdict_view(verdict))
    assert "≈ alpha@1" in text and "★ bravo@2" in text and "  charlie@1" in text
    assert "tie, broken on cost" in text.lower()


def test_rejected_snapshot():
    view = rejected_view(spec("reject", "Cover letters have no single right answer."))
    assert_snapshot("run_job_rejected", render(view))


def test_summary_adds_platform_and_provider_spend():
    subs = [
        Submission(
            task_id=f"t{i}",
            doc_id="0123456789ab",
            provider="alpha@1",
            status="ok",
            output={},
            latency_ms=10,
            cost_usd=0.002,
        )
        for i in range(3)
    ]
    text = render(summary_line(0.0005, subs, 12.34))
    assert text.strip() == (
        "Platform $0.0005  ·  Providers $0.0060  ·  Total $0.0065  ·  Runtime 12.3 s"
    )


# --- end to end -------------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def mock_env(tmp_path, monkeypatch):
    """Everything run_job reads or sets, pointed at tmp_path and free ports (monkeypatch
    restores every key afterwards, including those run_job overwrites)."""
    env = {
        "STORE_DIR": str(tmp_path / "store"),
        "DOCS_DIR": str(tmp_path / "docs"),
        "TRUTH_DIR": str(tmp_path / "truth"),
        "DOCS_BASE_URL": f"http://127.0.0.1:{free_port()}",
        "MOCK_PROVIDERS_BASE_URL": f"http://127.0.0.1:{free_port()}",
        "MOCK_PROVIDER_SLUGS": "mock-a,mock-b,mock-c,mock-d,mock-e",
        "MOCK_TASK_DEADLINE_MS": "300",
        "MAX_CONCURRENCY": "16",
        "SCORER": "real",
        "PROVIDERS_BASE_URL": "http://unused",
        "PROVIDER_SLUGS": "unused",
        "TASK_DEADLINE_MS": "60000",
        "SAMPLES_DIR": "unused",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return tmp_path


def run_mock(posting: str, reply: dict, store: JsonStore) -> tuple[int, str]:
    console = Console(record=True, width=100, color_system=None, force_terminal=False)
    code = asyncio.run(
        run(
            EXAMPLES_DIR / posting,
            console=console,
            mock=True,
            seed=7,
            store=store,
            gateway=FakeGateway(reply),
        )
    )
    return code, console.export_text()


def test_mock_run_ranks_the_mock_providers(mock_env):
    store = JsonStore(mock_env / "store")
    code, text = run_mock("invoice_clean.json", APPROVE, store)
    assert code == 0
    (record,) = [store.get_job(p.stem) for p in (mock_env / "store" / "jobs").glob("*.json")]
    assert record.status == "ranked"
    assert record.trial is not None and record.trial.trial_id == "trial-seed-7"
    assert os.environ["SCORER"] == "mock"
    for line in ("Job reviewed", "Trial built", "5 of 5 providers qualify", "Ranked"):
        assert line in text
    assert "3 timeout" in text  # mock-d sleeps past the deadline
    assert "Why" in text and "Runtime" in text  # seed 7 is a tie: no "Recommended:" headline


def test_mock_run_prints_the_rejection(mock_env):
    store = JsonStore(mock_env / "store")
    code, text = run_mock("reject_unscorable.json", REJECT, store)
    assert code == 0
    assert "Rejected by the Job Reviewer" in text
    assert REJECT["reason"] in text
    assert "Ranking" not in text and "Rubric" not in text
