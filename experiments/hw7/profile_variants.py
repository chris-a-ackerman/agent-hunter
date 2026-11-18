"""Profile-reading on card variants (AGE-63): the profile baseline, re-run with other agent cards.

    uv run python -m experiments.hw7.profile_variants --variant original|inflated|price_shuffled
        [--jobs all] [--n 5] [--run-id ID] [--mock] [--max-spend-usd 1.0]

For each job of `experiments/hw7/jobs/manifest.json` (manifest order), it loads the job's posting
and calls `pick_by_profile(posting, cards, gateway=..., job_id=f"hw7-{job}-v{variant}-p{k}")`
for k = 1..n, with the 7 cards of `experiments/hw7/cards/<variant>/`. No trial, no providers.

Outputs, in `experiments/hw7/runs/<run_id>/` (the run_benchmark layout):

    picks.jsonl       one row per pick, the run_benchmark profile-pick row with
                      `method: "profile:<variant>"`
    llm_calls.jsonl   every model call of this run (component, tokens, cost, job ID)

Several variants can share one run ID; `analyze --variants <run_id>` reads them all.

Controls, reused from `run_benchmark` (imported, not copied):

- `--max-spend-usd` caps the run's spend (its `RunLog.spend()`); `Harness.check_spend` runs
  before each pick, so once the cap is reached no new pick starts (exit 2).
- Rerunning with the same `--run-id` skips the picks already in `picks.jsonl`.
- `--mock`: `MockGateway` (no model calls, no cost, no network). It always answers the first
  card's slug.
- Live calls go through the gateway of this repo's `.env`; its `PARLEY_ACCOUNT` must be
  `--platform-account` (default: run_benchmark's `PLATFORM_ACCOUNT`).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from contracts import AgentCard, JobPosting
from experiments.hw7.analyze import AnalysisError, load_cards, provider_id
from experiments.hw7.baselines import pick_by_profile
from experiments.hw7.run_benchmark import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_PAUSED,
    EXIT_SPEND_CAP,
    JOBS_DIR,
    N_PROFILE,
    PLATFORM_ACCOUNT,
    RUNS_DIR,
    Harness,
    HarnessError,
    MockGateway,
    Options,
    Paths,
    RunGateway,
    RunLog,
    SpendCapReached,
    git_commit,
    select_jobs,
)
from gateway import BudgetExhausted, BudgetGuardTripped

log = logging.getLogger(__name__)

CARDS_ROOT = Path(__file__).parent / "cards"
VARIANTS = ("original", "inflated", "price_shuffled")
DEFAULT_MAX_SPEND_USD = 1.0


def method_for(variant: str) -> str:
    """The pick `method` of a variant, e.g. `profile:inflated`."""
    return f"profile:{variant}"


def job_id_for(job: str, variant: str, k: int) -> str:
    return f"hw7-{job}-v{variant}-p{k}"


@dataclass
class VariantOptions:
    variant: str
    jobs: str = "all"
    n: int = N_PROFILE
    run_id: str | None = None
    mock: bool = False
    max_spend_usd: float = DEFAULT_MAX_SPEND_USD
    platform_account: str = PLATFORM_ACCOUNT


def _postings(entries: list[dict[str, Any]], jobs_dir: Path) -> dict[str, JobPosting]:
    """Every selected job's posting, or one clear error naming the missing ones."""
    missing = [e["job"] for e in entries if not (jobs_dir / e["posting"]).is_file()]
    if missing:
        raise HarnessError(
            f"postings missing for {', '.join(missing)} in {jobs_dir}; run "
            "`uv run python -m experiments.hw7.make_jobs` (DocILE jobs need data/docile/, "
            "see README)"
        )
    return {
        e["job"]: JobPosting.model_validate_json(
            (jobs_dir / e["posting"]).read_text(encoding="utf-8")
        )
        for e in entries
    }


def _platform_gateway(opts: VariantOptions, cards: list[AgentCard]) -> Any:
    if opts.mock:
        return MockGateway(profile_slug=cards[0].slug)
    from core.agents.job_reviewer import default_gateway

    gateway = default_gateway()
    account = getattr(gateway, "account", None)
    if account != opts.platform_account:  # the same check as run_benchmark's
        raise HarnessError(
            f"platform calls would run on PARLEY_ACCOUNT={account!r}, not "
            f"{opts.platform_account!r}: put that account's key in .env "
            "(PARLEY_API_KEY, PARLEY_ACCOUNT) or pass --platform-account"
        )
    return gateway


def run_variant(
    opts: VariantOptions,
    *,
    jobs_dir: Path = JOBS_DIR,
    runs_dir: Path = RUNS_DIR,
    cards_root: Path = CARDS_ROOT,
    gateway: Any = None,
) -> tuple[int, Path]:
    """Log `opts.n` profile picks per job with the variant's cards; returns (exit code, run dir).
    `gateway` replaces the platform gateway (default: `MockGateway` with `mock`, else `.env`'s)."""
    if opts.variant not in VARIANTS:
        raise HarnessError(f"unknown variant {opts.variant!r}; one of {', '.join(VARIANTS)}")
    if opts.n < 1:
        raise HarnessError("--n must be at least 1")
    try:
        cards = load_cards(cards_root / opts.variant)
    except AnalysisError as e:
        raise HarnessError(str(e)) from e
    manifest = json.loads((jobs_dir / "manifest.json").read_text(encoding="utf-8"))["jobs"]
    entries = select_jobs(manifest, opts.jobs, None)
    postings = _postings(entries, jobs_dir)
    if gateway is None:
        gateway = _platform_gateway(opts, cards)

    commit, _ = git_commit()
    run_id = opts.run_id or ("mock-" if opts.mock else "") + "variants-" + datetime.now(
        UTC
    ).strftime("%Y%m%dT%H%M%SZ")
    runlog = RunLog(runs_dir / run_id, run_id, commit)
    gw = RunGateway(gateway, runlog)
    # The harness gives the spend cap (`check_spend`) and the pick row (`_pick`); no providers.
    harness = Harness(
        Options(jobs=opts.jobs, mock=opts.mock, max_spend_usd=opts.max_spend_usd, run_id=run_id),
        Paths(jobs_dir=jobs_dir, runs_dir=runs_dir),
        runlog,
        [],
        gw,
        None,
    )
    method = method_for(opts.variant)
    ids = {c.slug: provider_id(c) for c in cards}
    try:
        for entry in entries:
            job = entry["job"]
            for k in range(1, opts.n + 1):
                if runlog.has_pick(job, method, k):
                    continue
                harness.check_spend()
                job_id = job_id_for(job, opts.variant, k)
                pick = pick_by_profile(
                    postings[job], cards, gateway=gw.for_job(job_id), job_id=job_id
                )
                runlog.add_pick(
                    harness._pick(
                        entry,
                        method,
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
    except SpendCapReached as exc:
        log.error("spend cap: %s", exc)
        return EXIT_SPEND_CAP, runlog.dir
    except (BudgetExhausted, BudgetGuardTripped) as exc:
        log.error("platform budget: %s", exc)
        return EXIT_PAUSED, runlog.dir
    return EXIT_OK, runlog.dir


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    p = argparse.ArgumentParser(
        prog="python -m experiments.hw7.profile_variants", description=__doc__.split("\n")[0]
    )
    p.add_argument("--variant", choices=VARIANTS, required=True)
    p.add_argument("--jobs", choices=["tuning", "fresh", "all"], default="all")
    p.add_argument("--n", type=int, default=N_PROFILE, help="profile picks per job")
    p.add_argument("--run-id", help="the run directory name; reuse it to resume")
    p.add_argument("--mock", action="store_true", help="mock gateway: no model calls")
    p.add_argument("--max-spend-usd", type=float, default=DEFAULT_MAX_SPEND_USD)
    p.add_argument(
        "--platform-account",
        default=PLATFORM_ACCOUNT,
        help=f"PARLEY_ACCOUNT the model calls must run on (default {PLATFORM_ACCOUNT})",
    )
    a = p.parse_args(argv)
    if a.n < 1:
        p.error("--n must be at least 1")

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    opts = VariantOptions(
        variant=a.variant,
        jobs=a.jobs,
        n=a.n,
        run_id=a.run_id,
        mock=a.mock,
        max_spend_usd=a.max_spend_usd,
        platform_account=a.platform_account,
    )
    try:
        code, run_dir = run_variant(opts)
    except HarnessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    runlog = RunLog(run_dir, run_dir.name, "")
    method = method_for(a.variant)
    n_picks = sum(p["method"] == method for p in runlog.picks)
    print(
        f"{run_dir}: {n_picks} {method} picks · platform ${runlog.platform_spend():.4f} "
        f"({len(runlog.llm_calls)} calls)"
        + (" · stopped: spend cap" if code == EXIT_SPEND_CAP else "")
    )
    return code


if __name__ == "__main__":
    sys.exit(main())
