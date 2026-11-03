"""HW7 analysis (AGE-38): the benchmark logs → the results table, trial-size sweep and breakdowns.

    uv run python -m experiments.hw7.analyze --runs tuning-r1 [<run_id> ...] [--change off|on]
        [--out DIR]

Every definition follows `experiments/hw7/PREREG.md`; section numbers below refer to it.

Inputs, per run in `experiments/hw7/runs/<run_id>/`: `submissions.jsonl` (doc scores of every
trial and held-out answer), `picks.jsonl` (the logged picks) and `trials/*.json`, plus
`jobs/manifest.json` for each job's type and split. Several runs are pooled: their trial repeats
are kept apart by (run, repeat); held-out answers are the same pile and are deduplicated.

Re-ranking without re-scoring: for a subset of a trial's invoices, the logged doc scores are turned
back into `ScoreReport`s (CI from `bootstrap_ci(scores, seed=seed_for(trial_id))`, as the Scorer
does) and the logged answers into `Submission`s, then the real Ranker runs with `gateway=None`.

- **Ground truth (§3):** a provider's real accuracy = its mean held-out doc score; best = the max.
- **Trial sizes (§11):** 3 = the requester samples; 6 = samples + 3 of the 9 synthetic invoices,
  200 seeded draws (seed 0) per trial repeat; 12 = the whole trial.
- **Methods (§5):** Trial-then-Hire at 6 (the main row), profile-reading (mean of its runs),
  cheapest (logged pick) and random (expected value over all providers).
- **SQ (§6):** real accuracy of the pick / best. No pick (an `insufficient_candidates` verdict or
  a failed profile-reading run) scores 0 and counts as a miss.
- **Pass rule (§7)** on the as-shipped system (`--change off`) and all 12 jobs; with fewer jobs
  the verdict is printed as provisional.
- **Secondary (§8):** hit rate, bad picks, cost per correct invoice, time to hire.

Methods with several picks per job (Trial-then-Hire: repeats × draws; profile-reading: 5 runs;
random: 7 providers) are scored by their expected value per job: per-job SQ and hit rate are
means over those picks. Bad picks count jobs whose per-job SQ is < 0.80. Cost per correct invoice
is pooled over jobs: Σ expected cost ÷ Σ expected correct invoices.

Outputs, in `experiments/hw7/results/` (`--change on` → `results/change-on/`):

    main_table.md / .csv         the four methods, tuning and fresh in separate sections/rows
    sweep.csv                    Trial-then-Hire at sizes 3 / 6 / 12: SQ, tie rate, trial cost
    ground_truth.csv             each provider's real accuracy per job, and the best
    per_job.csv                  per-job SQ of every method and size
    noise.csv                    repeat r1 vs r2 per job
    sq_vs_trial_size.png         chart 1
    tie_rate_vs_trial_size.png   chart 2
    report.md                    all tables, the verdict and the checks in one page
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean
from typing import Any

import numpy as np

from contracts import DocScore, ScoreReport, Submission

HW7_DIR = Path(__file__).parent
RUNS_DIR = HW7_DIR / "runs"
JOBS_DIR = HW7_DIR / "jobs"
RESULTS_DIR = HW7_DIR / "results"

SIZES = (3, 6, 12)
MAIN_SIZE = 6  # the shipped trial (§5.1)
N_DRAWS = 200
DRAW_SEED = 0
N_BOOT = 10_000
BOOT_SEED = 0
HIT_MARGIN = 0.02
BAD_SQ = 0.80
CORRECT_DOC = 0.90
PASS_SQ = 0.90
PASS_DIFF = 0.05
N_JOBS_PREREG = 12

SPLITS = ("tuning", "fresh", "all")
METHODS = ("tth", "profile", "cheapest", "random")
METHOD_LABEL = {
    "tth": f"Trial-then-Hire ({MAIN_SIZE})",
    "profile": "Profile-reading",
    "cheapest": "Cheapest",
    "random": "Random (expected)",
}


class AnalysisError(Exception):
    pass


# --- loading ----------------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise AnalysisError(f"missing {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@dataclass
class TrialRepeat:
    """One logged 12-invoice trial of one job."""

    run_id: str
    repeat: int
    trial_id: str
    docs: list[str]  # trial order
    sources: dict[str, str]  # doc_id → "sample" | "synthetic" | ...
    rows: dict[tuple[str, str], dict[str, Any]]  # (provider, doc) → submission row
    providers: list[str]
    platform_cost: float
    selection_time_ms: float | None
    logged_pick: str | None
    logged_status: str | None

    @property
    def label(self) -> str:
        return f"{self.run_id}/r{self.repeat}"


@dataclass
class Job:
    job: str
    type: str
    split: str
    source: str
    near_tie: bool
    heldout_ids: list[str]
    heldout: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    repeats: list[TrialRepeat] = field(default_factory=list)
    picks: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))


def load(run_ids: Sequence[str], runs_dir: Path, manifest_path: Path) -> list[Job]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))["jobs"]
    jobs = {
        e["job"]: Job(
            job=e["job"],
            type=e["type"],
            split=e["split"],
            source=e["source"],
            near_tie=bool(e.get("near_tie")),
            heldout_ids=list(e["heldout_doc_ids"]),
        )
        for e in manifest
    }
    for run_id in run_ids:
        run_dir = runs_dir / run_id
        subs = _read_jsonl(run_dir / "submissions.jsonl")
        picks = _read_jsonl(run_dir / "picks.jsonl")
        trial_rows: dict[tuple[str, int], dict[tuple[str, str], dict[str, Any]]] = defaultdict(dict)
        for row in subs:
            job = jobs.get(row["job"])
            if job is None:
                raise AnalysisError(f"{run_id}: job {row['job']} is not in the manifest")
            if row["repeat"] == "heldout":
                job.heldout.setdefault((row["provider"], row["doc_id"]), row)
            else:
                trial_rows[(row["job"], int(row["repeat"]))][(row["provider"], row["doc_id"])] = row
        tth = {(p["job"], p["run"]): p for p in picks if p["method"] == "tth"}
        for p in picks:
            if p["method"] != "tth":
                jobs[p["job"]].picks[p["method"]].append(dict(p, run_id=run_id))
        for (job_id, r), rows in sorted(trial_rows.items()):
            pick = tth.get((job_id, r))
            if pick is None:
                raise AnalysisError(f"{run_id}: {job_id} r{r} has trial answers but no tth pick")
            trial_file = run_dir / "trials" / f"{job_id}-r{r}.json"
            if trial_file.is_file():
                trial = json.loads(trial_file.read_text(encoding="utf-8"))
                docs = [d["doc_id"] for d in trial["documents"]]
                sources = {d["doc_id"]: d["source"] for d in trial["documents"]}
            else:
                docs = sorted({d for _, d in rows})
                sources = {d: row["doc_source"] for (_, d), row in rows.items()}
            providers = sorted({p for p, _ in rows})
            missing = [(p, d) for p in providers for d in docs if (p, d) not in rows]
            if missing:
                raise AnalysisError(f"{run_id}: {job_id} r{r} is missing answers {missing[:3]}")
            trial_ids = {row["trial_id"] for row in rows.values()}
            if len(trial_ids) != 1:
                raise AnalysisError(f"{run_id}: {job_id} r{r} has several trial IDs {trial_ids}")
            jobs[job_id].repeats.append(
                TrialRepeat(
                    run_id=run_id,
                    repeat=r,
                    trial_id=trial_ids.pop(),
                    docs=docs,
                    sources=sources,
                    rows=rows,
                    providers=providers,
                    platform_cost=float(pick.get("platform_cost_usd") or 0.0),
                    selection_time_ms=pick.get("selection_time_ms"),
                    logged_pick=pick.get("picked"),
                    logged_status=pick.get("verdict_status"),
                )
            )
    return [j for j in jobs.values() if j.repeats or j.heldout]


# --- ground truth (§3) ------------------------------------------------------------------------


@dataclass
class Truth:
    accuracy: dict[str, float]  # provider → mean held-out score
    correct: dict[str, int]  # provider → held-out docs scored ≥ 0.90
    cost: dict[str, float]  # provider → Σ held-out cost_usd
    best: str
    best_accuracy: float

    def sq(self, provider: str | None) -> float:
        if provider is None:
            return 0.0
        if self.best_accuracy <= 0:
            return 1.0  # every provider scored 0: any pick is as good as the best
        return self.accuracy[provider] / self.best_accuracy

    def hit(self, provider: str | None) -> bool:
        return provider is not None and self.accuracy[provider] >= self.best_accuracy - HIT_MARGIN


def ground_truth(job: Job) -> Truth:
    providers = sorted({p for p, _ in job.heldout})
    if not providers:
        raise AnalysisError(f"{job.job}: no held-out answers")
    acc, correct, cost = {}, {}, {}
    for p in providers:
        rows = []
        for d in job.heldout_ids:
            row = job.heldout.get((p, d))
            if row is None:
                raise AnalysisError(f"{job.job}: no held-out answer from {p} for {d}")
            rows.append(row)
        acc[p] = fmean(r["score"] for r in rows)
        correct[p] = sum(r["score"] >= CORRECT_DOC for r in rows)
        cost[p] = sum(r["cost_usd"] or 0.0 for r in rows)
    best = max(providers, key=lambda p: (acc[p], -providers.index(p)))
    return Truth(acc, correct, cost, best, acc[best])


# --- re-ranking subsets (§5.1, §11) -------------------------------------------------------------


@dataclass
class Ranked:
    pick: str | None  # Verdict.recommended (on a tie, the tie-break winner)
    status: str
    n_tied: int
    leader: str | None  # highest trial accuracy (exploratory diagnostic, not a method)
    paired: str | None  # pick if ties used a paired test (exploratory diagnostic, not a method)


def rerank(rep: TrialRepeat, doc_ids: Sequence[str]) -> Ranked:
    """The real Ranker's verdict on `doc_ids` of a logged trial, from the logged scores."""
    from core.agents.ranker import rank
    from core.eval.stats import bootstrap_ci, seed_for

    seed = seed_for(rep.trial_id)
    subs: list[Submission] = []
    reports: list[ScoreReport] = []
    for p in rep.providers:
        docs = []
        for d in doc_ids:
            row = rep.rows[(p, d)]
            subs.append(
                Submission(
                    task_id=row["task_id"],
                    doc_id=d,
                    provider=p,
                    status=row["status"],
                    # the Ranker never reads outputs; the public repo blanks DocILE ones
                    output=(row["output"] or {}) if row["status"] == "ok" else None,
                    latency_ms=row["latency_ms"],
                    cost_usd=row["cost_usd"],
                )
            )
            docs.append(
                DocScore(
                    doc_id=d,
                    score=row["score"],
                    field_scores=row["field_scores"],
                    status=row["doc_status"],
                    sum_check_ok=row["sum_check_ok"],
                )
            )
        scores = [x.score for x in docs]
        reports.append(
            ScoreReport(
                provider=p,
                doc_scores=docs,
                trial_score=min(1.0, sum(scores) / len(scores)),
                ci_95=bootstrap_ci(scores, seed=seed),
                n_docs=len(docs),
            )
        )
    verdict = rank(reports, subs, rep.trial_id, gateway=None)
    answered = [r for r in verdict.ranking if r.mean_latency_ms is not None]
    leader = min(
        answered,
        key=lambda r: (-r.accuracy, r.cost_per_doc or math.inf, r.mean_latency_ms, r.provider),
        default=None,
    )
    return Ranked(
        pick=verdict.recommended,
        status=verdict.status,
        n_tied=len(verdict.tied),
        leader=leader.provider if leader else None,
        paired=_paired_pick(reports, answered, leader, seed),
    )


def _paired_pick(
    reports: list[ScoreReport], answered: list[Any], leader: Any, seed: int
) -> str | None:
    """What-if: a tie = the paired bootstrap CI of (leader − provider) per-doc scores includes 0
    (1,000 resamples, the trial's seed); the tie-break stays cost, latency, provider ID."""
    if leader is None:
        return None
    score = {r.provider: np.array([d.score for d in r.doc_scores]) for r in reports}
    n = len(score[leader.provider])
    idx = np.random.default_rng(seed).integers(0, n, size=(1000, n))

    def tied(r: Any) -> bool:
        diff = (score[leader.provider] - score[r.provider])[idx].mean(axis=1)
        return r is leader or float(np.quantile(diff, 0.025)) <= 0

    group = [r for r in answered if tied(r)]
    best = min(group, key=lambda r: (r.cost_per_doc or math.inf, r.mean_latency_ms, r.provider))
    return best.provider


def subsets(rep: TrialRepeat, size: int) -> list[list[str]]:
    """The doc subsets of one trial repeat at a trial size (§11)."""
    samples = [d for d in rep.docs if rep.sources[d] == "sample"]
    synthetic = [d for d in rep.docs if rep.sources[d] != "sample"]
    if size == len(rep.docs):
        return [list(rep.docs)]
    if size == len(samples):
        return [samples]
    k = size - len(samples)
    if not 0 < k <= len(synthetic):
        raise AnalysisError(f"{rep.label}: can't build a size-{size} trial")
    rng = np.random.default_rng(DRAW_SEED)
    out = []
    for _ in range(N_DRAWS):
        idx = sorted(rng.choice(len(synthetic), size=k, replace=False).tolist())
        out.append(samples + [synthetic[i] for i in idx])
    return out


# --- per-job outcomes -------------------------------------------------------------------------


@dataclass
class Pick:
    provider: str | None
    selection_cost: float
    time_s: float | None = None
    tie: bool | None = None
    trial_cost: float | None = None  # Trial-then-Hire only: provider cost of the subset
    repeat: str | None = None
    n_tied: int | None = None


@dataclass
class JobResult:
    job: Job
    truth: Truth
    picks: dict[str, list[Pick]]  # method key ("tth@3", "profile", ...) → picks
    rerank_matches: list[tuple[str, str | None, str | None]]  # (repeat, logged, re-ranked @12)

    def sq(self, key: str) -> float:
        return fmean(self.truth.sq(p.provider) for p in self.picks[key])

    def hit(self, key: str) -> float:
        return fmean(self.truth.hit(p.provider) for p in self.picks[key])

    def tie_rate(self, key: str) -> float:
        return fmean(bool(p.tie) for p in self.picks[key])

    def exp_cost(self, key: str) -> float:
        return fmean(
            p.selection_cost + (self.truth.cost[p.provider] if p.provider else 0.0)
            for p in self.picks[key]
        )

    def exp_correct(self, key: str) -> float:
        return fmean(self.truth.correct[p.provider] if p.provider else 0 for p in self.picks[key])

    def time_s(self, key: str) -> float | None:
        ts = [p.time_s for p in self.picks[key] if p.time_s is not None]
        return fmean(ts) if ts else None

    def trial_cost(self, key: str) -> float:
        return fmean(p.trial_cost or 0.0 for p in self.picks[key])

    def failed(self, key: str) -> int:
        return sum(p.provider is None for p in self.picks[key])


def analyse_job(job: Job) -> JobResult:
    truth = ground_truth(job)
    picks: dict[str, list[Pick]] = defaultdict(list)
    matches = []
    for rep in job.repeats:
        time_s = rep.selection_time_ms / 1000 if rep.selection_time_ms is not None else None
        cache: dict[tuple[str, ...], Ranked] = {}
        for size in SIZES:
            for docs in subsets(rep, size):
                key = tuple(sorted(docs))
                if key not in cache:
                    cache[key] = rerank(rep, docs)
                ranked = cache[key]
                trial_cost = sum(
                    rep.rows[(p, d)]["cost_usd"] or 0.0 for p in rep.providers for d in docs
                )
                for name, provider in (
                    ("tth", ranked.pick),
                    ("leader", ranked.leader),
                    ("paired", ranked.paired),
                ):
                    picks[f"{name}@{size}"].append(
                        Pick(
                            provider=provider,
                            selection_cost=trial_cost + rep.platform_cost,
                            time_s=time_s,  # from the 12-invoice run: an upper bound below 12
                            tie=ranked.status == "tie",
                            trial_cost=trial_cost,
                            repeat=rep.label,
                            n_tied=ranked.n_tied,
                        )
                    )
                if size == len(rep.docs):
                    matches.append((rep.label, rep.logged_pick, ranked.pick))
    for p in job.picks.get("profile", []):
        picks["profile"].append(
            Pick(
                provider=p.get("picked"),
                selection_cost=float(p.get("selection_cost_usd") or 0.0),
                time_s=(p["selection_time_ms"] / 1000) if p.get("selection_time_ms") else None,
            )
        )
    for p in job.picks.get("cheapest", [])[:1]:
        picks["cheapest"].append(Pick(provider=p.get("picked"), selection_cost=0.0, time_s=0.0))
    picks["random"] = [Pick(provider=p, selection_cost=0.0, time_s=0.0) for p in truth.accuracy]
    picks["tth"] = picks[f"tth@{MAIN_SIZE}"]
    return JobResult(job, truth, dict(picks), matches)


# --- aggregation over jobs --------------------------------------------------------------------


def boot_ci(
    values: Sequence[float], *, n: int = N_BOOT, seed: int = BOOT_SEED
) -> tuple[float, float]:
    """Percentile bootstrap 95% CI of the mean, resampling jobs."""
    x = np.asarray(values, dtype=float)
    if len(x) == 0:
        return math.nan, math.nan
    if len(x) == 1:
        return float(x[0]), float(x[0])
    rng = np.random.default_rng(seed)
    means = x[rng.integers(0, len(x), size=(n, len(x)))].mean(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return float(lo), float(hi)


def in_split(results: Iterable[JobResult], split: str) -> list[JobResult]:
    return [r for r in results if split == "all" or r.job.split == split]


@dataclass
class Row:
    split: str
    method: str
    n_jobs: int
    sq: float
    sq_lo: float
    sq_hi: float
    hit_rate: float
    bad_picks: int
    cost_per_correct: float | None
    time_s: float | None
    failed_runs: int | None
    tie_rate: float | None = None
    trial_cost: float | None = None


def aggregate(results: list[JobResult], key: str, split: str, method: str | None = None) -> Row:
    rs = [r for r in in_split(results, split) if r.picks.get(key)]
    sqs = [r.sq(key) for r in rs]
    lo, hi = boot_ci(sqs)
    cost = sum(r.exp_cost(key) for r in rs)
    correct = sum(r.exp_correct(key) for r in rs)
    times = [t for r in rs if (t := r.time_s(key)) is not None]
    is_tth = key.startswith("tth")
    return Row(
        split=split,
        method=method or key,
        n_jobs=len(rs),
        sq=fmean(sqs) if sqs else math.nan,
        sq_lo=lo,
        sq_hi=hi,
        hit_rate=fmean(r.hit(key) for r in rs) if rs else math.nan,
        bad_picks=sum(s < BAD_SQ for s in sqs),
        cost_per_correct=cost / correct if correct else None,
        time_s=fmean(times) if times else None,
        failed_runs=sum(r.failed(key) for r in rs) if key == "profile" else None,
        tie_rate=fmean(r.tie_rate(key) for r in rs) if is_tth and rs else None,
        trial_cost=fmean(r.trial_cost(key) for r in rs) if is_tth and rs else None,
    )


@dataclass
class Comparison:
    split: str
    n_jobs: int
    diff: float
    lo: float
    hi: float


def paired_diff(
    results: list[JobResult], split: str, a: str = "tth", b: str = "profile"
) -> Comparison:
    rs = [r for r in in_split(results, split) if r.picks.get(a) and r.picks.get(b)]
    d = [r.sq(a) - r.sq(b) for r in rs]
    lo, hi = boot_ci(d)
    return Comparison(split, len(rs), fmean(d) if d else math.nan, lo, hi)


def verdict(results: list[JobResult], change: str) -> str:
    """The §7 pass rule on the as-shipped system."""
    if change == "on":
        return (
            "Not judged: §7 judges the as-shipped system (`--change off`). These are the "
            "after-change numbers, reported next to the before numbers."
        )
    row = aggregate(results, "tth", "all")
    cmp = paired_diff(results, "all")
    c1 = row.sq >= PASS_SQ
    c2 = cmp.diff >= PASS_DIFF and cmp.lo > 0
    status = "SUPPORTED" if c1 and c2 else "NOT SUPPORTED"
    scope = (
        f"all {N_JOBS_PREREG} jobs"
        if row.n_jobs >= N_JOBS_PREREG
        else f"**provisional**: {row.n_jobs} of {N_JOBS_PREREG} jobs; the binding verdict "
        f"waits for the fresh jobs' before-change results"
    )
    return (
        f"**Claim {status}** ({scope}).\n\n"
        f"1. Trial-then-Hire mean SQ ≥ {PASS_SQ:.2f}: {_f(row.sq)} → "
        f"{'met' if c1 else 'not met'}.\n"
        f"2. Trial-then-Hire − profile-reading ≥ {PASS_DIFF:.2f} with the 95% CI above 0: "
        f"{_s(cmp.diff)} [{_s(cmp.lo)}, {_s(cmp.hi)}] → {'met' if c2 else 'not met'}."
    )


def diagnostics(results: list[JobResult]) -> list[dict[str, Any]]:
    out = []
    for split in splits_present(results):
        rs = in_split(results, split)
        for n in SIZES:
            key, lkey = f"tth@{n}", f"leader@{n}"
            rs_n = [r for r in rs if r.picks.get(key)]
            if not rs_n:
                continue
            lsq = [r.sq(lkey) for r in rs_n]
            out.append(
                {
                    "split": split,
                    "size": n,
                    "n_jobs": len(rs_n),
                    "tie_rate": fmean(r.tie_rate(key) for r in rs_n),
                    "tie_group": fmean(p.n_tied or 1 for r in rs_n for p in r.picks[key]),
                    "winner_not_leader": fmean(
                        a.provider != b.provider
                        for r in rs_n
                        for a, b in zip(r.picks[key], r.picks[lkey], strict=True)
                    ),
                    "sq": fmean(r.sq(key) for r in rs_n),
                    "leader_sq": fmean(lsq),
                    "leader_hit": fmean(r.hit(lkey) for r in rs_n),
                    "leader_bad": sum(s < BAD_SQ for s in lsq),
                    "paired_sq": fmean(r.sq(f"paired@{n}") for r in rs_n),
                }
            )
    return out


def trial_vs_real(r: JobResult) -> float:
    """Spearman ρ across providers: 12-invoice trial accuracy (mean of repeats) vs real accuracy."""
    from scipy.stats import spearmanr

    trial: dict[str, list[float]] = defaultdict(list)
    for rep in r.job.repeats:
        for p in rep.providers:
            trial[p].append(fmean(rep.rows[(p, d)]["score"] for d in rep.docs))
    ps = [p for p in r.truth.accuracy if p in trial]
    if len(ps) < 3:
        return math.nan
    rho = spearmanr([fmean(trial[p]) for p in ps], [r.truth.accuracy[p] for p in ps]).statistic
    return float(rho)


# --- outputs ----------------------------------------------------------------------------------


def _f(x: float | None, nd: int = 3) -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


def _s(x: float) -> str:
    return "–" if math.isnan(x) else f"{x:+.3f}"


def _usd(x: float | None) -> str:
    return "–" if x is None else f"${x:.4f}"


def _md_table(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(" -- " for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def _csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(["" if c is None else (round(c, 6) if isinstance(c, float) else c) for c in r])
    path.write_text(buf.getvalue(), encoding="utf-8")


def splits_present(results: list[JobResult]) -> list[str]:
    have = {r.job.split for r in results}
    out: list[str] = [s for s in ("tuning", "fresh") if s in have]
    return [*out, "all"] if len(out) > 1 else out


def main_rows(results: list[JobResult]) -> list[Row]:
    return [
        aggregate(results, m, s, METHOD_LABEL[m]) for s in splits_present(results) for m in METHODS
    ]


MAIN_HEADER = [
    "split",
    "method",
    "jobs",
    "mean SQ",
    "95% CI",
    "hit rate",
    "bad picks (SQ<0.80)",
    "cost per correct invoice",
    "time to hire (s)",
    "failed runs",
]


def _main_cells(r: Row) -> list[Any]:
    time = _f(r.time_s, 1) + (" (upper bound)" if r.method.startswith("Trial") else "")
    return [
        r.split,
        r.method,
        r.n_jobs,
        _f(r.sq),
        f"[{_f(r.sq_lo)}, {_f(r.sq_hi)}]",
        f"{r.hit_rate:.0%}",
        f"{r.bad_picks}/{r.n_jobs}",
        _usd(r.cost_per_correct),
        time,
        "–" if r.failed_runs is None else r.failed_runs,
    ]


def sweep_rows(results: list[JobResult]) -> list[Row]:
    return [
        aggregate(results, f"tth@{n}", s, f"tth@{n}")
        for s in splits_present(results)
        for n in SIZES
    ]


def render_main(results: list[JobResult], change: str, run_ids: Sequence[str]) -> str:
    rows = main_rows(results)
    parts = [
        f"# HW7 main table (change {change})",
        "",
        f"Runs: {', '.join(run_ids)}. Definitions: `experiments/hw7/PREREG.md` §6–§8; generated by "
        "`experiments/hw7/analyze.py`.",
        "",
    ]
    for split in splits_present(results):
        rs = [r for r in rows if r.split == split]
        rnd = next(r for r in rs if r.method.startswith("Random"))
        cmp = paired_diff(results, split)
        parts += [
            f"## {split.capitalize()} ({rs[0].n_jobs} jobs)",
            "",
            _md_table(MAIN_HEADER[1:], [_main_cells(r)[1:] for r in rs]),
            "",
            f"- Pass line SQ ≥ {PASS_SQ:.2f}; **random's expected SQ is {_f(rnd.sq)}**.",
            f"- Trial-then-Hire − profile-reading (paired bootstrap over jobs, {N_BOOT:,} "
            f"resamples, seed {BOOT_SEED}): **{_s(cmp.diff)}** [{_s(cmp.lo)}, {_s(cmp.hi)}].",
            "",
        ]
    parts += ["## Verdict (PREREG §7)", "", verdict(results, change), ""]
    parts += [
        "Notes: SQ and hit rate are expected values per job (Trial-then-Hire: 2 repeats × 200 "
        "subset draws; profile-reading: its 5 runs; random: all providers), then averaged over "
        "jobs. Cost per correct invoice = Σ over jobs of (selection cost + hired provider's "
        "held-out cost) ÷ Σ held-out invoices it scored ≥ 0.90. Trial-then-Hire's time to hire is "
        "measured on the 12-invoice run, so it's an upper bound for the 6-invoice trial.",
        "",
    ]
    return "\n".join(parts)


def write_outputs(
    results: list[JobResult], out: Path, change: str, run_ids: Sequence[str], *, charts: bool = True
) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    # main table
    main_md = render_main(results, change, run_ids)
    paths["main_md"] = out / "main_table.md"
    paths["main_md"].write_text(main_md, encoding="utf-8")
    paths["main_csv"] = out / "main_table.csv"
    _csv(
        paths["main_csv"],
        [
            "split",
            "method",
            "n_jobs",
            "mean_sq",
            "sq_ci_low",
            "sq_ci_high",
            "hit_rate",
            "bad_picks",
            "cost_per_correct_usd",
            "time_to_hire_s",
            "failed_runs",
        ],
        [
            [
                r.split,
                r.method,
                r.n_jobs,
                r.sq,
                r.sq_lo,
                r.sq_hi,
                r.hit_rate,
                r.bad_picks,
                r.cost_per_correct,
                r.time_s,
                r.failed_runs,
            ]
            for r in main_rows(results)
        ],
    )

    # sweep
    sweep = sweep_rows(results)
    paths["sweep_csv"] = out / "sweep.csv"
    _csv(
        paths["sweep_csv"],
        [
            "split",
            "trial_size",
            "n_jobs",
            "mean_sq",
            "sq_ci_low",
            "sq_ci_high",
            "hit_rate",
            "bad_picks",
            "tie_rate",
            "trial_cost_usd_per_job",
            "cost_per_correct_usd",
        ],
        [
            [
                r.split,
                int(r.method.split("@")[1]),
                r.n_jobs,
                r.sq,
                r.sq_lo,
                r.sq_hi,
                r.hit_rate,
                r.bad_picks,
                r.tie_rate,
                r.trial_cost,
                r.cost_per_correct,
            ]
            for r in sweep
        ],
    )

    # ground truth
    providers = sorted({p for r in results for p in r.truth.accuracy})
    paths["truth_csv"] = out / "ground_truth.csv"
    _csv(
        paths["truth_csv"],
        ["job", "type", "split", "source", *providers, "best", "best_accuracy"],
        [
            [
                r.job.job,
                r.job.type,
                r.job.split,
                r.job.source,
                *[r.truth.accuracy.get(p) for p in providers],
                r.truth.best,
                r.truth.best_accuracy,
            ]
            for r in results
        ],
    )

    # per job
    keys = [f"tth@{n}" for n in SIZES] + ["profile", "cheapest", "random"]
    paths["per_job_csv"] = out / "per_job.csv"
    _csv(
        paths["per_job_csv"],
        [
            "job",
            "type",
            "split",
            "source",
            "near_tie",
            "best",
            *[f"sq_{k}" for k in keys],
            *[f"tie_rate_tth@{n}" for n in SIZES],
            "profile_failed_runs",
        ],
        [
            [
                r.job.job,
                r.job.type,
                r.job.split,
                r.job.source,
                r.job.near_tie,
                r.truth.best,
                *[r.sq(k) if r.picks.get(k) else None for k in keys],
                *[r.tie_rate(f"tth@{n}") for n in SIZES],
                r.failed("profile"),
            ]
            for r in results
        ],
    )

    # noise
    noise = noise_rows(results)
    paths["noise_csv"] = out / "noise.csv"
    _csv(paths["noise_csv"], NOISE_HEADER, noise)

    if charts:
        paths.update(draw_charts(results, out, change))

    paths["report"] = out / "report.md"
    paths["report"].write_text(render_report(results, change, run_ids, main_md), encoding="utf-8")
    return paths


NOISE_HEADER = [
    "job",
    "repeat_a",
    "repeat_b",
    "pick_a@12",
    "pick_b@12",
    "same_pick@12",
    "sq_a@12",
    "sq_b@12",
    "abs_diff@12",
    "sq_a@6",
    "sq_b@6",
    "abs_diff@6",
]


def noise_rows(results: list[JobResult]) -> list[list[Any]]:
    rows = []
    for r in results:
        reps = sorted({p.repeat for p in r.picks.get("tth@12", []) if p.repeat})
        if len(reps) < 2:
            continue
        a, b = reps[0], reps[1]

        def by(size: int, rep: str, r: JobResult = r) -> list[Pick]:
            return [p for p in r.picks[f"tth@{size}"] if p.repeat == rep]

        pa, pb = by(12, a)[0].provider, by(12, b)[0].provider
        sq = {
            (n, x): fmean(r.truth.sq(p.provider) for p in by(n, x)) for n in (6, 12) for x in (a, b)
        }
        rows.append(
            [
                r.job.job,
                a,
                b,
                pa,
                pb,
                pa == pb,
                sq[(12, a)],
                sq[(12, b)],
                abs(sq[(12, a)] - sq[(12, b)]),
                sq[(6, a)],
                sq[(6, b)],
                abs(sq[(6, a)] - sq[(6, b)]),
            ]
        )
    return rows


def draw_charts(results: list[JobResult], out: Path, change: str) -> dict[str, Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {"tuning": "#2a6fdb", "fresh": "#d9822b", "all": "#333333"}
    sweep = sweep_rows(results)
    splits = splits_present(results)
    ref = "all" if "all" in splits else splits[0]
    paths = {}

    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)
    for split in splits:
        rs = [r for r in sweep if r.split == split]
        xs = [int(r.method.split("@")[1]) for r in rs]
        ys = [r.sq for r in rs]
        err = [[r.sq - r.sq_lo for r in rs], [r.sq_hi - r.sq for r in rs]]
        ax.errorbar(
            xs,
            ys,
            yerr=err,
            marker="o",
            capsize=4,
            color=colors[split],
            label=f"Trial-then-Hire ({split}, {rs[0].n_jobs} jobs)",
        )
        if split == ref:
            for x, y, r in zip(xs, ys, rs, strict=True):
                ax.annotate(
                    f"trial ${r.trial_cost:.2f}/job",
                    (x, y),
                    textcoords="offset points",
                    xytext=(6, -14),
                    fontsize=8,
                    color="#555555",
                )
    prof = aggregate(results, "profile", ref)
    rnd = aggregate(results, "random", ref)
    ax.axhline(prof.sq, color="#c0392b", ls="--", lw=1.2, label=f"Profile-reading ({_f(prof.sq)})")
    ax.axhline(rnd.sq, color="#7f8c8d", ls=":", lw=1.2, label=f"Random, expected ({_f(rnd.sq)})")
    ax.axhline(PASS_SQ, color="#27ae60", ls="-", lw=0.8, alpha=0.6, label=f"Pass line ({PASS_SQ})")
    ax.set_xticks(list(SIZES))
    ax.set_xlabel("Trial size (invoices)")
    ax.set_ylabel("Mean selection quality (SQ)")
    ax.set_title(f"Selection quality vs trial size (change {change}; 95% CI over jobs)")
    lo = min([r.sq_lo for r in sweep] + [prof.sq, rnd.sq, PASS_SQ])
    ax.set_ylim(max(0.0, lo - 0.05), 1.02)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    paths["chart_sq"] = out / "sq_vs_trial_size.png"
    fig.savefig(paths["chart_sq"])
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)
    ax2 = ax.twinx()
    diag = diagnostics(results)
    n_providers = max(len(r.truth.accuracy) for r in results)
    for split in splits:
        rs = [r for r in sweep if r.split == split]
        xs = [int(r.method.split("@")[1]) for r in rs]
        ax.plot(
            xs,
            [r.tie_rate or 0.0 for r in rs],
            marker="o",
            color=colors[split],
            label=f"tie rate, {split} ({rs[0].n_jobs} jobs)",
        )
        ax2.plot(
            [d["size"] for d in diag if d["split"] == split],
            [d["tie_group"] for d in diag if d["split"] == split],
            marker="s",
            ls="--",
            color=colors[split],
            alpha=0.6,
            label=f"tie-group size, {split}",
        )
    ax.set_xticks(list(SIZES))
    ax.set_ylim(0, 1.05)
    ax2.set_ylim(0, n_providers + 0.3)
    ax.set_xlabel("Trial size (invoices)")
    ax.set_ylabel("Share of verdicts that are a tie (solid)")
    ax2.set_ylabel(f"Mean providers in the tie group, of {n_providers} (dashed)")
    ax.set_title(f"Tie rate vs trial size (change {change})")
    ax.grid(alpha=0.25)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=8, loc="lower left")
    fig.tight_layout()
    paths["chart_tie"] = out / "tie_rate_vs_trial_size.png"
    fig.savefig(paths["chart_tie"])
    plt.close(fig)
    return paths


def render_report(
    results: list[JobResult], change: str, run_ids: Sequence[str], main_md: str
) -> str:
    keys = [f"tth@{n}" for n in SIZES] + ["profile", "cheapest", "random"]
    parts = [main_md, "## Trial-size sweep (Trial-then-Hire)", ""]
    parts.append(
        _md_table(
            [
                "split",
                "size",
                "mean SQ",
                "95% CI",
                "hit rate",
                "bad picks",
                "tie rate",
                "trial cost / job",
                "cost per correct invoice",
            ],
            [
                [
                    r.split,
                    r.method.split("@")[1],
                    _f(r.sq),
                    f"[{_f(r.sq_lo)}, {_f(r.sq_hi)}]",
                    f"{r.hit_rate:.0%}",
                    f"{r.bad_picks}/{r.n_jobs}",
                    f"{r.tie_rate:.0%}",
                    _usd(r.trial_cost),
                    _usd(r.cost_per_correct),
                ]
                for r in sweep_rows(results)
            ],
        )
    )
    parts += ["", "## Per job (SQ)", ""]
    parts.append(
        _md_table(
            ["job", "type", "split", "source", "best", *keys, "ties @6"],
            [
                [
                    r.job.job + (" (near tie)" if r.job.near_tie else ""),
                    r.job.type,
                    r.job.split,
                    r.job.source,
                    f"{r.truth.best} ({r.truth.best_accuracy:.3f})",
                    *[_f(r.sq(k)) if r.picks.get(k) else "–" for k in keys],
                    f"{r.tie_rate('tth@6'):.0%}",
                ]
                for r in results
            ],
        )
    )
    parts += ["", "## By job type (mean SQ)", ""]
    by_type: dict[str, list[JobResult]] = defaultdict(list)
    for r in results:
        by_type[r.job.type].append(r)
    parts.append(
        _md_table(
            ["type", "jobs", *keys, "ties @6"],
            [
                [
                    t,
                    len(rs),
                    *[_f(fmean(r.sq(k) for r in rs)) for k in keys],
                    f"{fmean(r.tie_rate('tth@6') for r in rs):.0%}",
                ]
                for t, rs in sorted(by_type.items())
            ],
        )
    )
    parts += [
        "",
        "## Real (DocILE) vs synthetic (descriptive, PREREG §9)",
        "",
        "The DocILE jobs' trials are mostly synthetic (3 real samples + 9 synthetic), but their "
        "held-out invoices are real, so their SQ is the closest check of whether the trial picks "
        "the best provider for real invoices. Too few jobs for a test.",
        "",
    ]
    real = [r for r in results if r.job.source == "docile"]
    synth = [r for r in results if r.job.source != "docile"]
    clean_synth = [r for r in synth if r.job.type == "clean"]
    other = [r for r in synth if r.job.type != "clean"]
    groups = [(r.job.job + " (real, clean)", [r]) for r in real]
    groups += [(r.job.job + " (synthetic, clean)", [r]) for r in clean_synth]
    if other:
        groups.append((f"other synthetic ({len(other)} jobs, mean)", other))
    parts.append(
        _md_table(
            ["jobs", *keys],
            [[name, *[_f(fmean(r.sq(k) for r in rs)) for k in keys]] for name, rs in groups],
        )
    )
    parts += ["", "## Run-to-run noise (trial repeats)", ""]
    parts.append(
        _md_table(
            [
                "job",
                "pick @12 (a)",
                "pick @12 (b)",
                "same?",
                "SQ a / b @12",
                "|Δ| @12",
                "SQ a / b @6",
                "|Δ| @6",
            ],
            [
                [
                    n[0],
                    n[3],
                    n[4],
                    "yes" if n[5] else "no",
                    f"{_f(n[6])} / {_f(n[7])}",
                    _f(n[8]),
                    f"{_f(n[9])} / {_f(n[10])}",
                    _f(n[11]),
                ]
                for n in noise_rows(results)
            ],
        )
    )
    parts += ["", "## Ground truth: real accuracy (mean held-out score) per provider", ""]
    providers = sorted({p for r in results for p in r.truth.accuracy})
    parts.append(
        _md_table(
            ["job", *providers],
            [
                [
                    r.job.job,
                    *[
                        (
                            f"**{r.truth.accuracy[p]:.3f}**"
                            if p == r.truth.best
                            else f"{r.truth.accuracy[p]:.3f}"
                        )
                        if p in r.truth.accuracy
                        else "–"
                        for p in providers
                    ],
                ]
                for r in results
            ],
        )
    )
    parts += [
        "",
        "## Diagnostics (exploratory, not pre-registered)",
        "",
        "How the Ranker reaches its pick. *Tie group* = providers whose CI overlaps the leader's; "
        "*leader* = highest trial accuracy. *Winner ≠ leader* = the cost tie-break picked someone "
        "other than the trial leader. *Leader SQ* is what picking the trial leader would have "
        "scored; *paired-test SQ* is what the shipped cost tie-break would have scored if a tie "
        "meant the paired bootstrap CI of (leader − provider) per-doc scores includes 0. Both are "
        "what-ifs for AGE-39, not pre-registered methods.",
        "",
    ]
    parts.append(
        _md_table(
            [
                "split",
                "size",
                "tie rate",
                "mean tie group (of 7)",
                "winner ≠ leader",
                "SQ (shipped)",
                "SQ (leader what-if)",
                "hit rate (leader)",
                "bad picks (leader)",
                "SQ (paired-test what-if)",
            ],
            [
                [
                    d["split"],
                    d["size"],
                    f"{d['tie_rate']:.0%}",
                    _f(d["tie_group"], 1),
                    f"{d['winner_not_leader']:.0%}",
                    _f(d["sq"]),
                    _f(d["leader_sq"]),
                    f"{d['leader_hit']:.0%}",
                    f"{d['leader_bad']}/{d['n_jobs']}",
                    _f(d["paired_sq"]),
                ]
                for d in diagnostics(results)
            ],
        )
    )
    parts += [
        "",
        "Trial vs held-out agreement per job: Spearman rank correlation, across providers, of "
        "trial accuracy (12 invoices, mean of the repeats) with real accuracy.",
        "",
    ]
    parts.append(
        _md_table(
            ["job", "type", "Spearman ρ", "best real accuracy", "providers within 0.02 of best"],
            [
                [
                    r.job.job,
                    r.job.type,
                    _f(trial_vs_real(r), 2),
                    _f(r.truth.best_accuracy),
                    sum(r.truth.hit(p) for p in r.truth.accuracy),
                ]
                for r in results
            ],
        )
    )
    mismatches = [
        (r.job.job, rep, logged, got)
        for r in results
        for rep, logged, got in r.rerank_matches
        if logged != got
    ]
    n_checked = sum(len(r.rerank_matches) for r in results)
    parts += [
        "",
        "## Checks",
        "",
        f"- Re-ranking the full 12-invoice trials from the logged scores reproduces the logged "
        f"pick in {n_checked - len(mismatches)}/{n_checked} trial repeats."
        + ("" if not mismatches else f" Mismatches: {mismatches}."),
        "",
    ]
    return "\n".join(parts)


# --- CLI --------------------------------------------------------------------------------------


def analyse(
    run_ids: Sequence[str], runs_dir: Path = RUNS_DIR, manifest: Path | None = None
) -> list[JobResult]:
    jobs = load(run_ids, runs_dir, manifest or JOBS_DIR / "manifest.json")
    if not jobs:
        raise AnalysisError("no jobs in these runs")
    return [analyse_job(j) for j in jobs]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", nargs="+", required=True, help="run IDs under experiments/hw7/runs/")
    ap.add_argument(
        "--change",
        choices=("off", "on"),
        default="off",
        help="off = as shipped (judged by PREREG §7); on = after the AGE-39 change",
    )
    ap.add_argument(
        "--out", type=Path, default=None, help="default: results/ (or results/change-on/)"
    )
    ap.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    ap.add_argument("--manifest", type=Path, default=JOBS_DIR / "manifest.json")
    ap.add_argument("--no-charts", action="store_true")
    args = ap.parse_args(argv)
    out = args.out or (RESULTS_DIR if args.change == "off" else RESULTS_DIR / "change-on")
    try:
        results = analyse(args.runs, args.runs_dir, args.manifest)
        paths = write_outputs(results, out, args.change, args.runs, charts=not args.no_charts)
    except AnalysisError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(paths["main_md"].read_text(encoding="utf-8"))
    for name in sorted(paths):
        print(f"wrote {paths[name]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
