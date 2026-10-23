"""run_job (AGE-17, plan §10.2): one job through the pipeline in-process, with a live terminal view.

    uv run python -m core.run_job examples/invoice_clean.json [--mock] [--seed N]

There's no UI in the MVP, so this is what the demo video records: a live timeline of the
pipeline steps, the providers reporting back one by one, then the ranking table with the
recommended provider highlighted, the Ranker's rationale and a cost/runtime summary (D19).

`--mock` forces `SCORER=mock`, points the registry at the mock providers (`MOCK_PROVIDERS_BASE_URL`,
`MOCK_PROVIDER_SLUGS`), starts them in a thread if nothing answers there yet and uses a short task
deadline (`MOCK_TASK_DEADLINE_MS`, default 5000). The Job Reviewer still makes its one real
model call. `--seed` fixes the trial ID, which seeds the Trial Runner's pacing and the mock scorer.

Exit code 0 for ranked / rejected / needs_info, 1 otherwise (error or paused).
"""

import argparse
import asyncio
import dataclasses
import functools
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from dotenv import load_dotenv
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.logging import RichHandler
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from contracts import (
    JobPosting,
    JobSpec,
    JobStatus,
    RegisteredProvider,
    RunEvent,
    Submission,
    TrialPackage,
    Verdict,
)
from core.store import JobRecord, JsonStore, Store

log = logging.getLogger(__name__)

MOCK_DEADLINE_MS = 5000

# (step, label while running, label when done), in pipeline order.
STEPS = [
    ("review", "Reviewing the job", "Job reviewed"),
    ("rubric", "Building the rubric", "Rubric built"),
    ("trial", "Building the trial", "Trial built"),
    ("eval", "Building the eval", "Eval ready"),
    ("candidates", "Finding candidates", "Candidates found"),
    ("trial_runner", "Running the trial", "Providers reported"),
    ("score", "Scoring", "Scored"),
    ("rank", "Ranking", "Ranked"),
]
DECISIONS = {
    "approve": "[green]approved[/]",
    "reject": "[red]rejected[/]",
    "needs_info": "[yellow]needs info[/]",
}


def _usd(x: float | None) -> str:
    return "—" if x is None else f"${x:.4f}"


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


# --- live view --------------------------------------------------------------------------------


@dataclasses.dataclass
class _Step:
    state: str = "pending"  # pending | running | done | stopped
    started: float = 0.0
    seconds: float | None = None
    detail: str = ""


@dataclasses.dataclass
class _Provider:
    done: int = 0
    counts: dict[str, int] | None = None  # set on provider_done


class LiveView:
    """The timeline, fed by `RunEvent`s (`handle`) and by the step taps in `run`; rendered by
    `rich.live.Live` through `__rich__`. Events arrive on the pipeline's thread, renders on
    Live's refresh thread, hence the lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.steps = {name: _Step() for name, _, _ in STEPS}
        self.providers: dict[str, _Provider] = {}
        self.n_docs = 0
        self.finished = False

    def handle(self, event: RunEvent) -> None:
        with self._lock:
            if event.kind == "task_done":
                self.providers.setdefault(event.detail["provider"], _Provider()).done += 1
                return
            if event.kind == "provider_done":
                p = self.providers.setdefault(event.detail["provider"], _Provider())
                p.counts = {k: event.detail.get(k, 0) for k in ("ok", "timeout", "error")}
                return
            step = self.steps.get(event.step)
            if step is None:
                return
            if event.kind == "started":
                step.state, step.started = "running", time.monotonic()
            elif event.kind == "finished":
                step.state = "done"
                step.seconds = event.detail.get("duration_ms", 0) / 1000
            elif event.kind in ("stopped", "paused"):
                step.state = "stopped"
                step.seconds = time.monotonic() - step.started

    def note(self, step: str, detail: str) -> None:
        with self._lock:
            self.steps[step].detail = detail

    def set_providers(self, providers: list[RegisteredProvider], n_docs: int) -> None:
        with self._lock:
            self.n_docs = n_docs
            for p in providers:
                self.providers.setdefault(p.provider_id, _Provider())

    def __rich__(self) -> RenderableType:
        with self._lock:
            return self._render()

    def _render(self) -> RenderableType:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(width=2)
        grid.add_column(min_width=22)
        grid.add_column(min_width=30)
        grid.add_column(justify="right", min_width=6)
        for name, running_label, done_label in STEPS:
            s = self.steps[name]
            if s.state == "pending" and self.finished:
                continue  # the run ended before this step (rejected, needs info, paused)
            seconds = s.seconds if s.seconds is not None else time.monotonic() - s.started
            secs = Text(f"{seconds:.1f} s", style="dim")
            if s.state == "done":
                detail = self._tasks_summary() if name == "trial_runner" else s.detail
                grid.add_row(Text("✓", style="bold green"), done_label, detail, secs)
            elif s.state == "running":
                grid.add_row(Spinner("dots", style="cyan"), Text(running_label, "bold"), "", secs)
            elif s.state == "stopped":
                grid.add_row(Text("✗", style="bold red"), Text(running_label, "red"), "", secs)
            else:
                grid.add_row(Text("·", style="dim"), Text(running_label, "dim"), "", "")
            if name == "trial_runner" and s.state != "pending":
                for pid, p in self.providers.items():
                    grid.add_row("", Text(f"  {pid}", "bold"), self._provider_status(p), "")
        return grid

    def _tasks_summary(self) -> str:
        counts = [p.counts for p in self.providers.values() if p.counts is not None]
        n = sum(sum(c.values()) for c in counts)
        return f"{n} tasks · {sum(c['ok'] for c in counts)} answered"

    def _provider_status(self, p: _Provider) -> Text:
        if p.counts is None:
            total = max(self.n_docs, p.done, 1)
            filled = round(12 * p.done / total)
            return Text.assemble(
                ("━" * filled, "cyan"), ("━" * (12 - filled), "grey35"), f" {p.done}/{total}"
            )
        parts = [("ok", "green"), ("timeout", "yellow"), ("error", "red")]
        clean = p.counts["timeout"] + p.counts["error"] == 0
        out = Text("✓ ", "green") if clean else Text("! ", "bold yellow")
        shown = [(f"{p.counts[k]} {k}", style) for k, style in parts if p.counts[k]]
        for i, (label, style) in enumerate(shown or [("no answers", "red")]):
            out.append(" · " if i else "")
            out.append(label, style)
        return out


# --- final views ------------------------------------------------------------------------------


def ranking_table(verdict: Verdict) -> Table:
    """The ranking: recommended row bold green (★), tied rows marked ≈."""
    table = Table(title="Ranking", title_style="bold", header_style="bold", min_width=72)
    table.add_column("#", justify="right")
    table.add_column("Provider")
    table.add_column("Accuracy", justify="right")
    table.add_column("95% CI", justify="right")
    table.add_column("Cost/doc", justify="right")
    table.add_column("Timeouts", justify="right")
    for i, row in enumerate(verdict.ranking, start=1):
        mark = (
            "★ "
            if row.provider == verdict.recommended
            else "≈ "
            if row.provider in verdict.tied
            else "  "
        )
        lo, hi = row.ci_95
        table.add_row(
            str(i),
            mark + row.provider,
            _pct(row.accuracy),
            f"{lo * 100:.1f} – {hi * 100:.1f}",
            _usd(row.cost_per_doc),
            str(row.timeouts),
            style="bold green" if row.provider == verdict.recommended else None,
        )
    if verdict.tie:
        table.caption = "≈ tie: confidence intervals overlap, broken on cost"
    return table


def verdict_view(verdict: Verdict) -> RenderableType:
    if verdict.status == "insufficient_candidates":
        headline = Text("No recommendation: fewer than two providers answered.", "bold yellow")
        border = "yellow"
    elif verdict.tie:
        headline = Text(f"Tie, broken on cost → hire {verdict.recommended}", "bold green")
        border = "green"
    else:
        headline = Text(f"Recommended: {verdict.recommended}", "bold green")
        border = "green"
    rationale = Panel(
        Group(headline, Text(""), Text(verdict.rationale)), title="Why", border_style=border
    )
    return Group(ranking_table(verdict), rationale)


def rejected_view(spec: JobSpec) -> Panel:
    return Panel(
        Text(spec.reason),
        title="[bold]Rejected by the Job Reviewer[/]",
        border_style="red",
        padding=(1, 2),
    )


def needs_info_view(spec: JobSpec) -> Panel:
    body = Text(spec.reason + "\n")
    for q in spec.questions:
        body.append(f"\n  • {q}")
    return Panel(
        body, title="[bold]The Job Reviewer needs more information[/]", border_style="yellow"
    )


def paused_view() -> Panel:
    return Panel(
        "Paused: a provider's model credit ran out. Submissions so far are saved.",
        border_style="yellow",
    )


def summary_line(platform_usd: float, submissions: list[Submission], runtime_s: float) -> Text:
    """D19: platform spend (our model calls) + provider spend (their reported cost_usd)."""
    providers = sum(s.cost_usd for s in submissions if s.cost_usd is not None)
    return Text.assemble(
        ("Platform ", "dim"),
        _usd(platform_usd),
        ("  ·  Providers ", "dim"),
        _usd(providers),
        ("  ·  Total ", "dim"),
        (_usd(platform_usd + providers), "bold"),
        ("  ·  Runtime ", "dim"),
        (f"{runtime_s:.1f} s", "bold"),
    )


def outcome_view(record: JobRecord) -> RenderableType | None:
    if record.status == JobStatus.RANKED and record.verdict is not None:
        return verdict_view(record.verdict)
    if record.status == JobStatus.REJECTED and record.spec is not None:
        return rejected_view(record.spec)
    if record.status == JobStatus.NEEDS_INFO and record.spec is not None:
        return needs_info_view(record.spec)
    if record.status == JobStatus.PAUSED:
        return paused_view()
    return None


# --- wiring -----------------------------------------------------------------------------------


class _JobGateway:
    """Tags every model call with the job ID, so `Store.platform_spend(job_id)` counts it (the
    `ReviewJob` step itself never sees the ID)."""

    def __init__(self, inner: Any, job_id: str) -> None:
        self.inner, self.job_id = inner, job_id

    def call(self, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("job_id", self.job_id)
        return self.inner.call(*args, **kwargs)


def _port(url: str, default: int) -> int:
    return urlsplit(url).port or default


def use_mock_providers() -> None:
    """--mock: mock scorer, mock provider registry, short deadline; start the mocks if needed."""
    from core.docserver import serve_app_in_thread
    from mocks.fake_providers import MOCKS, create_app

    base_url = os.environ.get("MOCK_PROVIDERS_BASE_URL") or "http://localhost:8101"
    slugs = os.environ.get("MOCK_PROVIDER_SLUGS") or ",".join(MOCKS)
    os.environ["SCORER"] = "mock"
    os.environ["PROVIDERS_BASE_URL"] = base_url
    os.environ["PROVIDER_SLUGS"] = slugs
    os.environ["TASK_DEADLINE_MS"] = os.environ.get("MOCK_TASK_DEADLINE_MS") or str(
        MOCK_DEADLINE_MS
    )
    app = create_app(slugs=[s.strip() for s in slugs.split(",") if s.strip()])
    try:
        serve_app_in_thread(app, port=_port(base_url, 8101), name="mock providers")
    except RuntimeError:
        log.info("mock providers already running at %s", base_url)


def _tap(fn: Callable[..., Any], after: Callable[..., Any]) -> Callable[..., Any]:
    """`fn`, then `after(result, *args)`; returns `after`'s result."""

    @functools.wraps(fn)
    def tapped(*args: Any) -> Any:
        return after(fn(*args), *args)

    return tapped


async def run(
    posting_path: Path,
    *,
    console: Console,
    mock: bool = False,
    seed: int | None = None,
    store: Store | None = None,
    gateway: Any = None,
) -> int:
    """Run the job in `posting_path` with the live view on `console`; returns the exit code.
    `gateway` replaces the Job Reviewer's model gateway (tests pass a `FakeGateway`)."""
    from core.agents.job_reviewer import default_gateway, review_job
    from core.docserver import serve_in_thread
    from core.pipeline.config import build_steps
    from core.pipeline.runner import run_pipeline

    started = time.monotonic()
    posting = JobPosting.model_validate_json(posting_path.read_text(encoding="utf-8"))
    samples_dir = posting_path.resolve().parent
    os.environ["SAMPLES_DIR"] = str(samples_dir)
    if mock:
        use_mock_providers()
    docs_url = os.environ.get("DOCS_BASE_URL") or "http://localhost:8200"
    try:
        serve_in_thread(port=_port(docs_url, 8200))
    except RuntimeError:
        log.info("doc server already running at %s", docs_url)

    store = store or JsonStore()
    job_id = store.create_job(posting)
    view = LiveView()

    def review(posting: JobPosting) -> JobSpec:
        gw = _JobGateway(gateway or default_gateway(), job_id)
        spec = review_job(posting, gateway=cast(Any, gw), samples_dir=samples_dir)
        view.note("review", DECISIONS[spec.decision])
        return spec

    def after_trial(trial: TrialPackage, *_: Any) -> TrialPackage:
        if seed is not None:
            trial = trial.model_copy(update={"trial_id": f"trial-seed-{seed}"})
        view.n_docs = len(trial.documents)
        view.note("trial", f"{len(trial.documents)} docs with known answers")
        return trial

    def after_candidates(
        kept: list[RegisteredProvider], _spec: JobSpec, registry: list[RegisteredProvider]
    ) -> list[RegisteredProvider]:
        view.set_providers(kept, view.n_docs)
        view.note("candidates", f"{len(kept)} of {len(registry)} providers qualify")
        return kept

    def after_rank(verdict: Verdict, *_: Any) -> Verdict:
        label = {"recommended": "clear winner", "tie": "tie", "insufficient_candidates": "—"}
        view.note("rank", label[verdict.status])
        return verdict

    base = build_steps(store)
    steps = dataclasses.replace(
        base,
        review=review,
        trial=_tap(base.trial, after_trial),
        candidates=_tap(base.candidates, after_candidates),
        rank=_tap(base.rank, after_rank),
    )

    console.rule("[bold]Agent-Hunter · Trial-then-Hire")
    console.print(
        Text.assemble(
            ("Job ", "dim"),
            (posting_path.name, "bold"),
            ("  ·  budget ", "dim"),
            f"${posting.job_budget_usd:.2f} for {posting.n_real_docs} docs",
            ("  ·  ", "dim"),
            (job_id, "dim"),
        )
    )
    console.print()
    error: Exception | None = None
    with Live(view, console=console, refresh_per_second=12):
        try:
            await run_pipeline(
                posting, store=store, steps=steps, on_event=view.handle, job_id=job_id
            )
        except Exception as exc:
            error = exc
        view.finished = True
    console.print()

    record = store.get_job(job_id)
    if error is not None:
        console.print(
            Panel(f"{type(error).__name__}: {error}", title="Stopped", border_style="red")
        )
    elif (outcome := outcome_view(record)) is not None:
        console.print(outcome)
    console.print(
        summary_line(store.platform_spend(job_id), record.submissions, time.monotonic() - started)
    )
    finished_ok = {JobStatus.RANKED, JobStatus.REJECTED, JobStatus.NEEDS_INFO}
    return 0 if error is None and record.status in finished_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m core.run_job", description=__doc__.split("\n")[0]
    )
    parser.add_argument(
        "posting", type=Path, help="a job posting JSON, e.g. examples/invoice_clean.json"
    )
    parser.add_argument("--mock", action="store_true", help="mock scorer and mock providers")
    parser.add_argument("--seed", type=int, help="fix the trial ID (pacing and mock scores)")
    args = parser.parse_args(argv)

    load_dotenv()
    console = Console()
    logging.basicConfig(
        level=logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False, show_time=False)],
    )
    return asyncio.run(run(args.posting, console=console, mock=args.mock, seed=args.seed))


if __name__ == "__main__":
    sys.exit(main())
