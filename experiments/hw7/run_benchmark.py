"""HW7 benchmark harness (AGE-36, experiments/hw7/PREREG.md): every method on every job, all logged.

    uv run python -m experiments.hw7.run_benchmark --jobs tuning|fresh|all [--repeat 2] [--mock]
        [--max-spend-usd 12] [--run-id ID] [--only j01,j02]

Per job (manifest order):

1. **Review:** `review_job(posting)`; the spec gets job ID `hw7-jNN`. Anything but `approve`
   stops that job with an error (the run goes on with the next job and exits non-zero).
2. **Rubric, built once:** `experiments/hw7/rubrics/jNN.json` if it exists, else `build_rubric`
   and save it (commit it). `--mock` keeps its rubrics inside the run directory instead.
3. **Trial-then-Hire, repeats r = 1..N** (job ID `hw7-jNN-rR`): Trial Builder (12 invoices) →
   Eval Builder → Candidate Finder → Trial Runner → Scorer → Ranker (`gateway=None`, so no
   model-written rationale). Each step is timed; time to hire = review start → verdict.
4. **Ground truth:** the 12 held-out invoices, as a separate batch, to every registered provider,
   scored with the same rubric. Once per job: a (provider, doc) pair already answered in any run
   under `experiments/hw7/runs/` is copied (`cached_from`), never resent.
5. **Baselines:** `pick_by_profile` 5× (job IDs `hw7-jNN-p1..p5`) and `pick_cheapest`.

Outputs, in `experiments/hw7/runs/<run_id>/`:

    submissions.jsonl   one row per (provider, doc): output, doc score, field scores, latency, cost
    picks.jsonl         one row per pick: method tth | profile | cheapest
    llm_calls.jsonl     every platform model call of this run (component, tokens, cost, job ID)
    trials/jNN-rR.json  the trial's doc IDs and sources (no answer keys; those stay in data/truth)
    summary.json        spend, calls, wall clock, failures by job type, commits and settings

Controls:

- `--max-spend-usd` caps Σ submission `cost_usd` + platform model cost of the run. It's checked
  before each batch of provider calls and each job; over it, no new batch starts, the summary is
  written and the exit code is 2.
- A provider's 402 (`RunPaused`) or the gateway's budget guard stops the run (exit 3); the
  submissions that came back are kept, the unsent ones aren't logged against the provider.
- Rerunning with the same `--run-id` skips every (provider, doc) pair already in
  `submissions.jsonl` and every pick already in `picks.jsonl`; a finished job isn't re-reviewed.
- `--mock`: the mock providers (`core.run_job.use_mock_providers`), a fake gateway for the
  platform and baseline calls (no model calls, no cost), the real Scorer, no PDF rendering.

Platform model calls go through the gateway of this repo's `.env` (`PARLEY_API_KEY`,
`PARLEY_ACCOUNT`), which must be Nathalie's account (`--platform-account` overrides the check);
the providers service runs on its own key. Settings are the MVP's: `TASK_DEADLINE_MS` (60 s),
`MAX_CONCURRENCY` (24), pacing on.
"""

import argparse
import asyncio
import functools
import json
import logging
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from contracts import (
    Invoice,
    JobPosting,
    JobSpec,
    RegisteredProvider,
    Rubric,
    RunEvent,
    RunPaused,
    ScoringConfig,
    Submission,
    TrialDoc,
    TrialPackage,
)
from gateway import DEFAULT_MODEL, BudgetExhausted, BudgetGuardTripped, GatewayResponse

log = logging.getLogger(__name__)

HW7_DIR = Path(__file__).parent
JOBS_DIR = HW7_DIR / "jobs"
RUBRICS_DIR = HW7_DIR / "rubrics"
RUNS_DIR = HW7_DIR / "runs"
PROVIDERS_COMMIT = "5d9536a"  # agent cards frozen here (PREREG §12)
PLATFORM_ACCOUNT = "nathalie"
TRIAL_SIZE = 12
N_PROFILE = 5
PACING_MAX_DELAY_S = 1.5  # the Trial Runner's default (D23)

EXIT_OK, EXIT_ERROR, EXIT_SPEND_CAP, EXIT_PAUSED = 0, 1, 2, 3


class HarnessError(Exception):
    """A clear stop: missing inputs, a job the reviewer didn't approve, a broken resume."""


class SpendCapReached(Exception):
    pass


# --- files ------------------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def git_commit() -> tuple[str, bool]:
    """(short HEAD, has uncommitted changes); ("unknown", False) outside git."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown", False
    return head, bool(dirty)


class RunLog:
    """The run directory: append-only JSONL logs plus the resume indexes built from them."""

    def __init__(self, run_dir: Path, run_id: str, commit: str) -> None:
        self.dir, self.run_id, self.commit = run_dir, run_id, commit
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "trials").mkdir(exist_ok=True)
        self.submissions_path = run_dir / "submissions.jsonl"
        self.picks_path = run_dir / "picks.jsonl"
        self.llm_calls_path = run_dir / "llm_calls.jsonl"
        self.submissions = _read_jsonl(self.submissions_path)
        self.picks = _read_jsonl(self.picks_path)
        self.llm_calls = _read_jsonl(self.llm_calls_path)

    # submissions

    def done(self, job: str, repeat: int | str) -> dict[tuple[str, str], dict[str, Any]]:
        """(provider, doc_id) → row, for one job's trial repeat or `"heldout"`."""
        return {
            (r["provider"], r["doc_id"]): r
            for r in self.submissions
            if r["job"] == job and r["repeat"] == repeat
        }

    def add_submissions(self, rows: list[dict[str, Any]]) -> None:
        with self.submissions_path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.submissions.extend(rows)

    # picks

    def has_pick(self, job: str, method: str, run: int) -> bool:
        return any(
            p["job"] == job and p["method"] == method and p["run"] == run for p in self.picks
        )

    def add_pick(self, row: dict[str, Any]) -> None:
        with self.picks_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.picks.append(row)

    # platform calls

    def add_llm_call(self, row: dict[str, Any]) -> None:
        with self.llm_calls_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        self.llm_calls.append(row)

    def platform_cost(self, *job_ids: str) -> float:
        ids = set(job_ids)
        return sum(c["cost_usd"] for c in self.llm_calls if c.get("job_id") in ids)

    # spend

    def provider_spend(self) -> float:
        """What the providers billed in this run (rows copied from earlier runs cost nothing)."""
        return sum(r["cost_usd"] or 0.0 for r in self.submissions if r.get("cached_from") is None)

    def platform_spend(self) -> float:
        return sum(
            c["cost_usd"] for c in self.llm_calls if str(c.get("job_id", "")).startswith("hw7-")
        )

    def spend(self) -> float:
        return self.provider_spend() + self.platform_spend()


# --- gateways ---------------------------------------------------------------------------------


class RunGateway:
    """Tags each model call with a job ID (when the step doesn't pass one, e.g. the Job
    Reviewer) and logs it to the run's `llm_calls.jsonl`, so platform spend is per run and job."""

    def __init__(self, inner: Any, runlog: RunLog, job_id: str = "hw7") -> None:
        self.inner, self.runlog, self.job_id = inner, runlog, job_id

    def for_job(self, job_id: str) -> Any:  # duck-types `gateway.Gateway` for the steps
        return RunGateway(self.inner, self.runlog, job_id)

    def call(self, component: str, model: str, messages: list[dict[str, Any]], **kw: Any) -> Any:
        kw["job_id"] = kw.get("job_id") or self.job_id
        resp: GatewayResponse = self.inner.call(component, model, messages, **kw)
        self.runlog.add_llm_call(
            {
                "ts": _now(),
                "account": getattr(self.inner, "account", "mock"),
                "component": component,
                "model": model,
                "tokens_in": resp.tokens_in,
                "tokens_out": resp.tokens_out,
                "cost_usd": resp.cost_usd,
                "job_id": kw["job_id"],
            }
        )
        return resp


# Replies of the fake gateway used by --mock, per component.
MOCK_REVIEW = {"decision": "approve", "reason": "Invoice extraction to JSON.", "questions": []}
MOCK_RUBRIC = {
    "weights": {
        "vendor_name": 0.15,
        "invoice_number": 0.1,
        "invoice_date": 0.15,
        "currency": 0.1,
        "total": 0.25,
        "line_items": 0.25,
    },
    "required_fields": ["vendor_name", "invoice_date", "currency", "total"],
}
MOCK_GEN_SETTINGS = {"layout": "classic", "currency": "USD", "line_items": [1, 4]}


class MockGateway:
    """--mock: one scripted `FakeGateway` reply per call, picked by component. The profile
    baseline always answers `profile_slug`."""

    account = "mock"

    def __init__(self, profile_slug: str) -> None:
        self.replies: dict[str, dict[str, Any]] = {
            "job_reviewer": MOCK_REVIEW,
            "rubric_builder": MOCK_RUBRIC,
            "trial_builder": MOCK_GEN_SETTINGS,
            "baseline_profile": {"slug": profile_slug, "reason": "Mock pick."},
        }

    def call(self, component: str, model: str, messages: list[dict[str, Any]], **kw: Any) -> Any:
        from mocks.fake_gateway import FakeGateway

        return FakeGateway(self.replies[component]).call(component, model, messages, **kw)


# --- inputs -----------------------------------------------------------------------------------


@dataclass
class Paths:
    jobs_dir: Path = JOBS_DIR
    rubrics_dir: Path = RUBRICS_DIR
    runs_dir: Path = RUNS_DIR
    docs_dir: Path = field(default_factory=lambda: Path(os.environ.get("DOCS_DIR") or "data/docs"))
    truth_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("TRUTH_DIR") or "data/truth")
    )


def select_jobs(manifest: list[dict[str, Any]], jobs: str, only: list[str] | None) -> list[dict]:
    known = {e["job"] for e in manifest}
    unknown = sorted(set(only or []) - known)
    if unknown:
        raise HarnessError(f"unknown jobs in --only: {', '.join(unknown)}")
    picked = [e for e in manifest if jobs == "all" or e["split"] == jobs]
    if only:
        picked = [e for e in picked if e["job"] in only]
    if not picked:
        raise HarnessError(f"no jobs selected (--jobs {jobs}, --only {','.join(only or [])})")
    return picked


def preflight(entries: list[dict[str, Any]], paths: Paths, *, need_pdfs: bool) -> None:
    """Every posting, sample and held-out invoice must be on disk; else one clear error."""
    problems: list[str] = []
    for e in entries:
        job, hint = (
            e["job"],
            (
                "download DocILE into data/docile/ (see README) and run "
                "`uv run python -m experiments.hw7.make_jobs`"
                if e["source"] == "docile"
                else "run `uv run python -m experiments.hw7.make_jobs`"
            ),
        )
        posting_path = paths.jobs_dir / e["posting"]
        if not posting_path.is_file():
            problems.append(f"{job}: posting {posting_path} is missing; {hint}")
            continue
        posting = JobPosting.model_validate_json(posting_path.read_text(encoding="utf-8"))
        missing = [s.path for s in posting.samples if not (paths.jobs_dir / s.path).is_file()]
        if missing:
            problems.append(f"{job}: samples missing ({', '.join(missing)}); {hint}")
        gone = [
            d
            for d in e["heldout_doc_ids"]
            if not (paths.truth_dir / f"{d}.json").is_file()
            or (need_pdfs and not (paths.docs_dir / f"{d}.pdf").is_file())
        ]
        if gone:
            problems.append(f"{job}: {len(gone)} held-out invoices missing; {hint}")
    if problems:
        raise HarnessError("inputs missing:\n  " + "\n  ".join(problems))


def save_trial(path: Path, trial: TrialPackage) -> None:
    """Doc IDs and sources only: the answer keys stay in TRUTH_DIR."""
    _write_json(
        path,
        {
            "trial_id": trial.trial_id,
            "job_id": trial.job_id,
            "created_at": trial.created_at.isoformat(),
            "documents": [{"doc_id": d.doc_id, "source": d.source} for d in trial.documents],
        },
    )


def load_trial(path: Path, truth_dir: Path) -> TrialPackage:
    data = json.loads(path.read_text(encoding="utf-8"))
    docs = []
    for d in data["documents"]:
        truth = truth_dir / f"{d['doc_id']}.json"
        if not truth.is_file():
            raise HarnessError(f"resume: answer key {truth} of trial {path.name} is gone")
        docs.append(
            TrialDoc(
                doc_id=d["doc_id"],
                source=d["source"],
                answer=Invoice.model_validate_json(truth.read_text(encoding="utf-8")),
            )
        )
    return TrialPackage(
        trial_id=data["trial_id"],
        job_id=data["job_id"],
        documents=docs,
        created_at=datetime.fromisoformat(data["created_at"]),
    )


# --- the harness ------------------------------------------------------------------------------


@dataclass
class Options:
    jobs: str = "all"
    only: list[str] | None = None
    repeat: int = 2
    mock: bool = False
    max_spend_usd: float = 12.0
    run_id: str | None = None
    platform_account: str = PLATFORM_ACCOUNT


class Harness:
    def __init__(
        self,
        opts: Options,
        paths: Paths,
        runlog: RunLog,
        registry: list[RegisteredProvider],
        gateway: RunGateway,
        runner: Any,
    ) -> None:
        self.opts, self.paths, self.log = opts, paths, runlog
        self.registry, self.gw, self.runner = registry, gateway, runner
        self.rubrics_dir = runlog.dir / "rubrics" if opts.mock else paths.rubrics_dir
        self.job_seconds: dict[str, float] = {}

    def check_spend(self) -> None:
        spent = self.log.spend()
        if spent >= self.opts.max_spend_usd:
            raise SpendCapReached(
                f"spent ${spent:.4f} of the ${self.opts.max_spend_usd:.2f} cap; no new work started"
            )

    def job_complete(self, job: str, n_heldout: int) -> bool:
        picks = [("tth", r) for r in range(1, self.opts.repeat + 1)]
        picks += [("profile", k) for k in range(1, N_PROFILE + 1)] + [("cheapest", 1)]
        if not all(self.log.has_pick(job, m, r) for m, r in picks):
            return False
        return len(self.log.done(job, "heldout")) >= n_heldout * len(self.registry)

    async def run_job(self, entry: dict[str, Any]) -> None:
        job = entry["job"]
        if self.job_complete(job, len(entry["heldout_doc_ids"])):
            log.info("%s: already complete in this run; skipped", job)
            return
        self.check_spend()
        started = time.monotonic()
        base = f"hw7-{job}"
        posting = JobPosting.model_validate_json(
            (self.paths.jobs_dir / entry["posting"]).read_text(encoding="utf-8")
        )
        from core.agents.job_reviewer import review_job

        spec, review_ms = await _timed(
            review_job, posting, gateway=self.gw.for_job(base), samples_dir=self.paths.jobs_dir
        )
        if spec.decision != "approve":
            raise HarnessError(f"{job}: the Job Reviewer said {spec.decision!r}: {spec.reason}")
        spec = spec.model_copy(update={"job_id": base})
        rubric, rubric_ms, cached = await self.rubric(job, spec)
        head = {"review": review_ms, "rubric": rubric_ms, "rubric_cached": cached}

        for r in range(1, self.opts.repeat + 1):
            await self.trial_then_hire(entry, posting, spec, rubric, r, head, started)
        await self.heldout(entry, posting, rubric)
        await self.baselines(entry, posting, spec)
        self.job_seconds[job] = self.job_seconds.get(job, 0.0) + time.monotonic() - started

    async def rubric(self, job: str, spec: JobSpec) -> tuple[Rubric, int, bool]:
        path = self.rubrics_dir / f"{job}.json"
        t0 = time.monotonic()
        if path.is_file():
            rubric = Rubric.model_validate_json(path.read_text(encoding="utf-8"))
            return rubric, _ms(t0), True
        from core.agents.rubric_builder import build_rubric

        rubric, ms = await _timed(build_rubric, spec, gateway=self.gw.for_job(spec.job_id))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rubric.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return rubric, ms, False

    async def trial_then_hire(
        self,
        entry: dict[str, Any],
        posting: JobPosting,
        spec: JobSpec,
        rubric: Rubric,
        r: int,
        head: dict[str, Any],
        job_started: float,
    ) -> None:
        from core.agents.ranker import rank
        from core.agents.trial_builder import build_trial
        from core.eval.eval_builder import build_eval
        from core.eval.scorer import score
        from core.runner.candidate_finder import find_candidates

        job = entry["job"]
        if self.log.has_pick(job, "tth", r):
            return
        self.check_spend()
        resumed = bool(self.log.done(job, r))
        spec_r = spec.model_copy(update={"job_id": f"{spec.job_id}-r{r}"})
        trial_path = self.log.dir / "trials" / f"{job}-r{r}.json"
        steps: dict[str, Any] = dict(head)
        t0 = time.monotonic()
        if trial_path.is_file():
            trial = load_trial(trial_path, self.paths.truth_dir)
            steps["trial"], steps["trial_cached"] = _ms(t0), True
        else:
            trial, steps["trial"] = await _timed(
                build_trial,
                spec_r,
                posting,
                size=TRIAL_SIZE,
                gateway=self.gw.for_job(spec_r.job_id),
                samples_dir=self.paths.jobs_dir,
                docs_dir=self.paths.docs_dir,
                truth_dir=self.paths.truth_dir,
                render=not self.opts.mock,
            )
            save_trial(trial_path, trial)
        config, steps["eval"] = await _timed(
            build_eval, rubric.model_copy(update={"job_id": trial.job_id}), trial
        )
        t0 = time.monotonic()
        candidates = find_candidates(spec_r, self.registry)
        steps["candidates"] = _ms(t0)
        if not candidates:
            raise HarnessError(f"{job}: no provider passed the Candidate Finder")

        t0 = time.monotonic()
        subs = await self.send(entry, posting, trial, config, candidates, repeat=r)
        steps["trial_runner"] = _ms(t0)
        t0 = time.monotonic()
        reports = score(config, subs)
        steps["score"] = _ms(t0)
        t0 = time.monotonic()
        verdict = rank(reports, subs, trial.trial_id, gateway=None)
        steps["rank"] = _ms(t0)

        provider_cost = sum(s.cost_usd or 0.0 for s in subs)
        platform = self.log.platform_cost(spec.job_id, spec_r.job_id)
        self.log.add_pick(
            self._pick(
                entry,
                "tth",
                r,
                picked=verdict.recommended,
                verdict_status=verdict.status,
                tied=verdict.tied,
                reason=verdict.rationale,
                attempts=None,
                failed=verdict.recommended is None,
                selection_cost_usd=provider_cost + platform,
                provider_cost_usd=provider_cost,
                platform_cost_usd=platform,
                # review start → verdict; review and rubric are shared by the job's repeats
                selection_time_ms=round(
                    head["review"]
                    + head["rubric"]
                    + sum(
                        steps[k]
                        for k in ("trial", "eval", "candidates", "trial_runner", "score", "rank")
                    )
                ),
                job_elapsed_ms=_ms(job_started),
                steps_ms=steps,
                trial_id=trial.trial_id,
                n_candidates=len(candidates),
                resumed=resumed,
            )
        )

    async def heldout(self, entry: dict[str, Any], posting: JobPosting, rubric: Rubric) -> None:
        from core.eval.eval_builder import build_eval

        job = entry["job"]
        ids = entry["heldout_doc_ids"]
        if len(self.log.done(job, "heldout")) >= len(ids) * len(self.registry):
            return
        source = "trial_real" if entry["source"] == "docile" else "synthetic"
        trial_id = f"hw7-{job}-heldout"
        trial = TrialPackage(
            trial_id=trial_id,
            job_id=trial_id,
            documents=[
                TrialDoc(
                    doc_id=d,
                    source=source,
                    answer=Invoice.model_validate_json(
                        (self.paths.truth_dir / f"{d}.json").read_text(encoding="utf-8")
                    ),
                )
                for d in ids
            ],
            created_at=datetime.now(UTC),
        )
        config = build_eval(rubric.model_copy(update={"job_id": trial_id}), trial)
        self.reuse_heldout(entry)
        self.check_spend()
        await self.send(entry, posting, trial, config, self.registry, repeat="heldout")

    def reuse_heldout(self, entry: dict[str, Any]) -> None:
        """Copy held-out answers from earlier runs, so no held-out doc reaches a provider twice."""
        job = entry["job"]
        have = self.log.done(job, "heldout")
        ids = set(entry["heldout_doc_ids"])
        providers = {p.provider_id for p in self.registry}
        copied: list[dict[str, Any]] = []
        for other in sorted(self.paths.runs_dir.glob("*/submissions.jsonl")):
            if other.parent == self.log.dir:
                continue
            for row in _read_jsonl(other):
                key = (row["provider"], row["doc_id"])
                if (
                    row["job"] == job
                    and row["repeat"] == "heldout"
                    and row["doc_id"] in ids
                    and row["provider"] in providers
                    and key not in have
                ):
                    origin = row.get("cached_from") or row["run_id"]
                    have[key] = row = dict(row, run_id=self.log.run_id, cached_from=origin)
                    copied.append(row)
        if copied:
            log.info("%s: %d held-out answers reused from earlier runs", job, len(copied))
            self.log.add_submissions(copied)

    async def send(
        self,
        entry: dict[str, Any],
        posting: JobPosting,
        trial: TrialPackage,
        config: ScoringConfig,
        providers: list[RegisteredProvider],
        *,
        repeat: int | str,
    ) -> list[Submission]:
        """Every (provider, doc) pair of `trial` not yet logged goes through the Trial Runner;
        new answers are scored and logged. Returns all the trial's submissions, in trial order."""
        from core.eval.scorer import score

        job = entry["job"]
        done = self.log.done(job, repeat)
        groups: dict[tuple[str, ...], list[RegisteredProvider]] = defaultdict(list)
        for p in providers:
            todo = tuple(d.doc_id for d in trial.documents if (p.provider_id, d.doc_id) not in done)
            if todo:
                groups[todo].append(p)

        for doc_ids, group in groups.items():
            self.check_spend()
            wanted = set(doc_ids)
            batch = trial.model_copy(
                update={"documents": [d for d in trial.documents if d.doc_id in wanted]}
            )
            finished: dict[tuple[str, str], str] = {}

            def on_event(e: RunEvent, finished: dict[tuple[str, str], str] = finished) -> None:
                if e.kind == "task_done":
                    finished[(e.detail["provider"], e.detail["doc_id"])] = _now()

            paused: RunPaused | None = None
            try:
                subs = await self.runner(batch, group, posting.requester_id, on_event)
            except RunPaused as exc:
                paused, subs = exc, exc.submissions
            sent = [s for s in subs if s.status != "not_run"]
            rows = self._rows(entry, trial, repeat, sent, config, score, finished)
            self.log.add_submissions(rows)
            done.update({(r["provider"], r["doc_id"]): r for r in rows})
            if paused is not None:
                raise paused

        return [
            _submission(done[(p.provider_id, d.doc_id)]) for p in providers for d in trial.documents
        ]

    def _rows(
        self,
        entry: dict[str, Any],
        trial: TrialPackage,
        repeat: int | str,
        subs: list[Submission],
        config: ScoringConfig,
        score: Callable[..., Any],
        finished: dict[tuple[str, str], str],
    ) -> list[dict[str, Any]]:
        if not subs:
            return []
        scored = {(r.provider, d.doc_id): d for r in score(config, subs) for d in r.doc_scores}
        sources = {d.doc_id: d.source for d in trial.documents}
        rows = []
        for s in subs:
            ds = scored[(s.provider, s.doc_id)]
            rows.append(
                {
                    "run_id": self.log.run_id,
                    "git_commit": self.log.commit,
                    "job": entry["job"],
                    "job_source": entry["source"],
                    "job_type": entry["type"],
                    "repeat": repeat,
                    "arm": "heldout" if repeat == "heldout" else "trial",
                    "trial_id": trial.trial_id,
                    "provider": s.provider,
                    "doc_id": s.doc_id,
                    "doc_source": "heldout" if repeat == "heldout" else sources[s.doc_id],
                    "task_id": s.task_id,
                    "status": s.status,
                    "output": s.output,
                    "score": ds.score,
                    "field_scores": ds.field_scores,
                    "doc_status": ds.status,
                    "sum_check_ok": ds.sum_check_ok,
                    "latency_ms": s.latency_ms,
                    "cost_usd": s.cost_usd,
                    "finished_at": finished.get((s.provider, s.doc_id)) or _now(),
                }
            )
        return rows

    async def baselines(self, entry: dict[str, Any], posting: JobPosting, spec: JobSpec) -> None:
        from core.runner.candidate_finder import find_candidates
        from experiments.hw7.baselines import pick_by_profile, pick_cheapest

        job = entry["job"]
        candidates = find_candidates(spec, self.registry)
        cards = [p.card for p in candidates]
        ids = {p.card.slug: p.provider_id for p in candidates}
        for k in range(1, N_PROFILE + 1):
            if self.log.has_pick(job, "profile", k):
                continue
            self.check_spend()
            job_id = f"{spec.job_id}-p{k}"
            pick = await asyncio.to_thread(
                pick_by_profile, posting, cards, gateway=self.gw.for_job(job_id), job_id=job_id
            )
            self.log.add_pick(
                self._pick(
                    entry,
                    "profile",
                    k,
                    picked=ids.get(pick.slug) if pick.slug else None,
                    verdict_status=None,
                    tied=[],
                    reason=pick.reason,
                    attempts=pick.attempts,
                    failed=pick.failed,
                    selection_cost_usd=pick.cost_usd,
                    selection_time_ms=pick.latency_ms,
                    tokens_in=pick.tokens_in,
                    tokens_out=pick.tokens_out,
                )
            )
        if not self.log.has_pick(job, "cheapest", 1):
            t0 = time.monotonic()
            slug = pick_cheapest(cards)
            self.log.add_pick(
                self._pick(
                    entry,
                    "cheapest",
                    1,
                    picked=ids[slug],
                    verdict_status=None,
                    tied=[],
                    reason="lowest list price per document",
                    attempts=None,
                    failed=False,
                    selection_cost_usd=0.0,
                    selection_time_ms=_ms(t0),
                )
            )

    def _pick(self, entry: dict[str, Any], method: str, run: int, **fields: Any) -> dict:
        return {
            "run_id": self.log.run_id,
            "git_commit": self.log.commit,
            "job": entry["job"],
            "job_source": entry["source"],
            "job_type": entry["type"],
            "method": method,
            "run": run,
            **fields,
            "finished_at": _now(),
        }


def _submission(row: dict[str, Any]) -> Submission:
    return Submission(
        task_id=row["task_id"],
        doc_id=row["doc_id"],
        provider=row["provider"],
        status=row["status"],
        output=row["output"],
        latency_ms=row["latency_ms"],
        cost_usd=row["cost_usd"],
    )


def _ms(t0: float) -> int:
    return round((time.monotonic() - t0) * 1000)


async def _timed[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> tuple[T, int]:
    """`fn` in a worker thread (it may call a model), with its duration in ms."""
    t0 = time.monotonic()
    result = await asyncio.to_thread(functools.partial(fn, *args, **kwargs))
    return result, _ms(t0)


# --- summary ----------------------------------------------------------------------------------


def summarize(
    h: Harness,
    *,
    entries: list[dict[str, Any]],
    started_at: str,
    wall_s: float,
    stopped: str | None,
    errors: list[str],
    dirty: bool,
) -> dict[str, Any]:
    runlog = h.log
    previous = {}
    path = runlog.dir / "summary.json"
    if path.is_file():
        previous = json.loads(path.read_text(encoding="utf-8"))
    types = {e["job"]: e["type"] for e in entries}

    failures: dict[str, Counter[str]] = defaultdict(Counter)
    by_job: dict[str, dict[str, float]] = defaultdict(lambda: {"provider_usd": 0.0})
    fresh = [r for r in runlog.submissions if r.get("cached_from") is None]
    for r in runlog.submissions:
        c = failures[r["job_type"]]
        c["tasks"] += 1
        c[r["doc_status"]] += 1  # ok | invalid | timeout | error
    for r in fresh:
        by_job[r["job"]]["provider_usd"] += r["cost_usd"] or 0.0
    for call in runlog.llm_calls:
        jid = str(call.get("job_id", ""))
        if jid.startswith("hw7-"):
            job = jid.split("-")[1]
            by_job[job]["platform_usd"] = by_job[job].get("platform_usd", 0.0) + call["cost_usd"]

    seconds_by_type: dict[str, float] = defaultdict(float)
    for job, s in h.job_seconds.items():
        seconds_by_type[types.get(job, "?")] += s
    prev_wall = previous.get("wall_clock_s_total", 0.0)
    return {
        "run_id": runlog.run_id,
        "git_commit": runlog.commit,
        "git_dirty": dirty,
        "providers_commit": PROVIDERS_COMMIT,
        "mock": h.opts.mock,
        "started_at": started_at,
        "finished_at": _now(),
        "wall_clock_s": round(wall_s, 1),
        "wall_clock_s_total": round(prev_wall + wall_s, 1),
        "args": {
            "jobs": h.opts.jobs,
            "only": h.opts.only,
            "repeat": h.opts.repeat,
            "max_spend_usd": h.opts.max_spend_usd,
        },
        "settings": {
            "task_deadline_ms": h.runner.deadline_ms,
            "max_concurrency": h.runner.max_concurrency,
            "pacing_max_delay_s": h.runner.max_delay_s,
            "trial_size": TRIAL_SIZE,
            "profile_runs": N_PROFILE,
            "model": DEFAULT_MODEL,
            "scorer": "real",
            "platform_account": getattr(h.gw.inner, "account", None),
            "providers": [p.provider_id for p in h.registry],
        },
        "jobs": [e["job"] for e in entries],
        "jobs_completed": [
            e["job"] for e in entries if h.job_complete(e["job"], len(e["heldout_doc_ids"]))
        ],
        "stopped": stopped,
        "errors": errors,
        "spend": {
            "provider_usd": round(runlog.provider_spend(), 6),
            "platform_usd": round(runlog.platform_spend(), 6),
            "total_usd": round(runlog.spend(), 6),
            "provider_calls": len(fresh),
            "provider_rows_reused": len(runlog.submissions) - len(fresh),
            "platform_calls": len(runlog.llm_calls),
            "by_job": {
                j: {k: round(v, 6) for k, v in d.items()} for j, d in sorted(by_job.items())
            },
        },
        "failures_by_job_type": {t: dict(c) for t, c in sorted(failures.items())},
        "seconds_by_job": {j: round(s, 1) for j, s in sorted(h.job_seconds.items())},
        "seconds_by_job_type": {t: round(s, 1) for t, s in sorted(seconds_by_type.items())},
    }


# --- entry point ------------------------------------------------------------------------------


def _port(url: str, default: int) -> int:
    from urllib.parse import urlsplit

    return urlsplit(url).port or default


async def run_benchmark(
    opts: Options, *, paths: Paths | None = None, gateway: Any = None
) -> tuple[int, Path]:
    """Run the benchmark; returns (exit code, run directory). `gateway` replaces the platform
    gateway (default: `MockGateway` with --mock, else the job store's cost-logging gateway)."""
    from core.docserver import serve_in_thread
    from core.runner.registry import refresh_registry
    from core.runner.trial_runner import TrialRunner

    paths = paths or Paths()
    started, started_at = time.monotonic(), _now()
    manifest = json.loads((paths.jobs_dir / "manifest.json").read_text(encoding="utf-8"))["jobs"]
    entries = select_jobs(manifest, opts.jobs, opts.only)
    preflight(entries, paths, need_pdfs=not opts.mock)

    if opts.mock:
        from core.run_job import use_mock_providers

        use_mock_providers()
    os.environ["SCORER"] = "real"
    docs_url = os.environ.get("DOCS_BASE_URL") or "http://localhost:8200"
    try:
        serve_in_thread(port=_port(docs_url, 8200), root=paths.docs_dir)
    except RuntimeError:
        log.info("doc server already running at %s", docs_url)

    registry = await refresh_registry()
    if not registry:
        raise HarnessError(
            "no provider answered at PROVIDERS_BASE_URL; start the providers service (README)"
        )
    if gateway is None:
        if opts.mock:
            gateway = MockGateway(profile_slug=registry[0].card.slug)
        else:
            from core.agents.job_reviewer import default_gateway

            gateway = default_gateway()
            account = getattr(gateway, "account", None)
            if account != opts.platform_account:
                raise HarnessError(
                    f"platform calls would run on PARLEY_ACCOUNT={account!r}, not "
                    f"{opts.platform_account!r}: put that account's key in .env "
                    "(PARLEY_API_KEY, PARLEY_ACCOUNT) or pass --platform-account"
                )

    commit, dirty = git_commit()
    run_id = opts.run_id or ("mock-" if opts.mock else "") + datetime.now(UTC).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    runlog = RunLog(paths.runs_dir / run_id, run_id, commit)
    harness = Harness(
        opts, paths, runlog, registry, RunGateway(gateway, runlog), TrialRunner.from_env()
    )

    stopped: str | None = None
    errors: list[str] = []
    code = EXIT_OK
    try:
        for entry in entries:
            try:
                await harness.run_job(entry)
            except HarnessError as exc:
                log.error("%s", exc)
                errors.append(str(exc))
                code = EXIT_ERROR
    except SpendCapReached as exc:
        log.error("spend cap: %s", exc)
        stopped, code = "spend_cap", EXIT_SPEND_CAP
        errors.append(str(exc))
    except RunPaused as exc:
        log.error("a provider ran out of model credit (402); run paused")
        stopped, code = "paused", EXIT_PAUSED
        errors.append(f"provider budget exhausted: {exc.reason}")
    except (BudgetExhausted, BudgetGuardTripped) as exc:
        log.error("platform budget: %s", exc)
        stopped, code = "platform_budget", EXIT_PAUSED
        errors.append(f"platform budget: {exc}")
    except Exception as exc:
        stopped, code = "error", EXIT_ERROR
        errors.append(f"{type(exc).__name__}: {exc}")
        raise
    finally:
        summary = summarize(
            harness,
            entries=entries,
            started_at=started_at,
            wall_s=time.monotonic() - started,
            stopped=stopped,
            errors=errors,
            dirty=dirty,
        )
        _write_json(runlog.dir / "summary.json", summary)
    return code, runlog.dir


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    p = argparse.ArgumentParser(
        prog="python -m experiments.hw7.run_benchmark", description=__doc__.split("\n")[0]
    )
    p.add_argument("--jobs", choices=["tuning", "fresh", "all"], default="all")
    p.add_argument("--only", help="comma-separated job IDs, e.g. j01,j02 (within --jobs)")
    p.add_argument("--repeat", type=int, default=2, help="Trial-then-Hire repeats per job")
    p.add_argument("--mock", action="store_true", help="mock providers and gateway, real Scorer")
    p.add_argument("--max-spend-usd", type=float, default=12.0)
    p.add_argument("--run-id", help="the run directory name; reuse it to resume")
    p.add_argument(
        "--platform-account",
        default=PLATFORM_ACCOUNT,
        help=f"PARLEY_ACCOUNT the platform calls must run on (default {PLATFORM_ACCOUNT})",
    )
    a = p.parse_args(argv)
    if a.repeat < 1:
        p.error("--repeat must be at least 1")

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    opts = Options(
        jobs=a.jobs,
        only=[j.strip() for j in a.only.split(",") if j.strip()] if a.only else None,
        repeat=a.repeat,
        mock=a.mock,
        max_spend_usd=a.max_spend_usd,
        run_id=a.run_id,
        platform_account=a.platform_account,
    )
    try:
        code, run_dir = asyncio.run(run_benchmark(opts))
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    s = summary["spend"]
    print(
        f"{run_dir}: providers ${s['provider_usd']:.4f} ({s['provider_calls']} calls) · platform "
        f"${s['platform_usd']:.4f} ({s['platform_calls']} calls) · {summary['wall_clock_s']} s"
        + (f" · stopped: {summary['stopped']}" if summary["stopped"] else "")
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
