"""The HW7 analysis (AGE-38) on a hand-made log: known picks → known SQ, hit rate, cost."""

import json
import os
from pathlib import Path

import pytest

from experiments.hw7 import analyze as an

PROVIDERS = ["alpha@0.1.0", "bravo@0.1.0", "charlie@0.1.0"]
SAMPLES = [f"{i:012x}" for i in range(1, 4)]  # doc IDs are 12 hex chars
SYNTH = [f"{i:012x}" for i in range(0x10, 0x19)]
HELDOUT = ["0000000000a1", "0000000000a2"]
REAL = {
    "alpha@0.1.0": 0.9,
    "bravo@0.1.0": 0.6,
    "charlie@0.1.0": 0.3,
}  # held-out score of every doc → real accuracy
COST = 0.01  # every task
PLATFORM = 0.001


def _row(job, repeat, trial_id, provider, doc, source, score, status="ok"):
    return {
        "run_id": "t",
        "job": job,
        "job_source": "synthetic",
        "job_type": "clean",
        "repeat": repeat,
        "arm": "heldout" if repeat == "heldout" else "trial",
        "trial_id": trial_id,
        "provider": provider,
        "doc_id": doc,
        "doc_source": source,
        "task_id": f"{provider}-{doc}-{repeat}",
        "status": status,
        "output": {"x": 1} if status == "ok" else None,
        "score": score,
        "field_scores": {"total": score},
        "doc_status": status,
        "sum_check_ok": None,
        "latency_ms": 100 if status == "ok" else 60000,
        "cost_usd": COST if status == "ok" else None,
    }


def _pick(job, method, run, picked, **kw):
    return {"run_id": "t", "job": job, "method": method, "run": run, "picked": picked, **kw}


def write_run(tmp_path: Path, *, only_a_answers: bool = False) -> tuple[Path, Path]:
    """One tuning job j01: in the trial alpha@0.1.0 scores 1.0 and the others 0.0 (or time out)."""
    runs, run = tmp_path / "runs", tmp_path / "runs" / "t"
    (run / "trials").mkdir(parents=True)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "job": "j01",
                        "type": "clean",
                        "split": "tuning",
                        "source": "synthetic",
                        "near_tie": False,
                        "heldout_doc_ids": HELDOUT,
                    }
                ]
            }
        )
    )
    trial_id = "trial-test"
    docs = [(d, "sample") for d in SAMPLES] + [(d, "synthetic") for d in SYNTH]
    (run / "trials" / "j01-r1.json").write_text(
        json.dumps(
            {
                "trial_id": trial_id,
                "documents": [{"doc_id": d, "source": s} for d, s in docs],
            }
        )
    )
    rows = []
    for p in PROVIDERS:
        for d, src in docs:
            if p == "alpha@0.1.0":
                rows.append(_row("j01", 1, trial_id, p, d, src, 1.0))
            elif only_a_answers:
                rows.append(_row("j01", 1, trial_id, p, d, src, 0.0, status="timeout"))
            else:
                rows.append(_row("j01", 1, trial_id, p, d, src, 0.0))
        for d in HELDOUT:
            rows.append(_row("j01", "heldout", "ho", p, d, "heldout", REAL[p]))
    (run / "submissions.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    picks = [
        _pick(
            "j01",
            "tth",
            1,
            None if only_a_answers else "alpha@0.1.0",
            verdict_status="insufficient_candidates" if only_a_answers else "recommended",
            platform_cost_usd=PLATFORM,
            selection_time_ms=40_000,
        ),
        *[
            _pick(
                "j01",
                "profile",
                k,
                "bravo@0.1.0",
                selection_cost_usd=0.0002,
                selection_time_ms=2000,
            )
            for k in range(1, 6)
        ],
        _pick("j01", "cheapest", 1, "charlie@0.1.0", selection_cost_usd=0.0, selection_time_ms=0),
    ]
    (run / "picks.jsonl").write_text("\n".join(json.dumps(p) for p in picks) + "\n")
    return runs, manifest


def test_known_picks_give_known_sq(tmp_path):
    runs, manifest = write_run(tmp_path)
    [r] = an.analyse(["t"], runs, manifest)

    assert r.truth.best == "alpha@0.1.0"
    assert r.truth.accuracy == pytest.approx(REAL)
    assert r.rerank_matches == [
        ("t/r1", "alpha@0.1.0", "alpha@0.1.0")
    ]  # the re-ranked 12 match the log
    for n in an.SIZES:
        assert {p.provider for p in r.picks[f"tth@{n}"]} == {"alpha@0.1.0"}
        assert r.sq(f"tth@{n}") == 1.0
        assert r.tie_rate(f"tth@{n}") == 0.0
    assert len(r.picks["tth@6"]) == an.N_DRAWS
    assert r.sq("profile") == pytest.approx(0.6 / 0.9)
    assert r.sq("cheapest") == pytest.approx(0.3 / 0.9)
    assert r.sq("random") == pytest.approx((1 + 0.6 / 0.9 + 0.3 / 0.9) / 3)

    tth = an.aggregate([r], "tth", "tuning")
    assert (tth.hit_rate, tth.bad_picks, tth.time_s) == (1.0, 0, 40.0)
    # (6-invoice trial: 3 providers × 6 docs + platform + alpha@0.1.0's 2 held-out docs) / 2 correct
    assert tth.cost_per_correct == pytest.approx((3 * 6 * COST + PLATFORM + 2 * COST) / 2)
    prof = an.aggregate([r], "profile", "tuning")
    assert (prof.hit_rate, prof.bad_picks, prof.failed_runs) == (0.0, 1, 0)
    assert prof.cost_per_correct is None  # bravo@0.1.0 never scores ≥ 0.90
    cmp = an.paired_diff([r], "tuning")
    assert cmp.diff == pytest.approx(1 - 0.6 / 0.9)
    assert "SUPPORTED" in an.verdict([r], "off") and "provisional" in an.verdict([r], "off")
    assert "Not judged" in an.verdict([r], "on")


def test_six_invoice_subsets_keep_the_samples(tmp_path):
    runs, manifest = write_run(tmp_path)
    [job] = an.load(["t"], runs, manifest)
    subs = an.subsets(job.repeats[0], 6)
    assert len(subs) == an.N_DRAWS
    assert all(s[:3] == SAMPLES and set(s[3:]) <= set(SYNTH) and len(set(s)) == 6 for s in subs)
    assert subs == an.subsets(job.repeats[0], 6)  # seeded
    assert an.subsets(job.repeats[0], 3) == [SAMPLES]


def test_no_recommendation_scores_zero(tmp_path):
    runs, manifest = write_run(tmp_path, only_a_answers=True)
    [r] = an.analyse(["t"], runs, manifest)
    assert r.picks["tth@12"][0].provider is None
    assert r.sq("tth") == 0.0
    assert an.aggregate([r], "tth", "tuning").bad_picks == 1


def test_main_writes_tables_and_charts(tmp_path):
    runs, manifest = write_run(tmp_path)
    out = tmp_path / "results"
    argv = ["--runs", "t", "--runs-dir", str(runs), "--manifest", str(manifest), "--out", str(out)]
    assert an.main(argv) == 0
    for name in [
        "main_table.md",
        "main_table.csv",
        "sweep.csv",
        "ground_truth.csv",
        "per_job.csv",
        "noise.csv",
        "report.md",
        "sq_vs_trial_size.png",
        "tie_rate_vs_trial_size.png",
    ]:
        assert (out / name).stat().st_size > 0, name
    table = (out / "main_table.md").read_text()
    assert "Trial-then-Hire (6) | 1 | 1.000" in table
    assert "random's expected SQ is 0.667" in table


def test_unknown_run_is_a_clear_error(tmp_path):
    runs, manifest = write_run(tmp_path)
    argv = ["--runs", "nope", "--runs-dir", str(runs), "--manifest", str(manifest)]
    assert an.main(argv) == 1


@pytest.mark.parametrize("change", ["off", "on"])
def test_change_flag_sets_the_env_var(tmp_path, monkeypatch, change):
    monkeypatch.delenv(an.CHANGE_ENV, raising=False)
    runs, manifest = write_run(tmp_path)
    argv = ["--runs", "t", "--runs-dir", str(runs), "--manifest", str(manifest)]
    argv += ["--out", str(tmp_path / "out"), "--no-charts", "--change", change]
    assert an.main(argv) == 0
    assert os.environ[an.CHANGE_ENV] == change
    assert ("Not judged" in (tmp_path / "out" / "main_table.md").read_text()) == (change == "on")
