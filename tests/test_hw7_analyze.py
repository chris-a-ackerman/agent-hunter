"""The HW7 analysis (AGE-38) on a hand-made log: known picks → known SQ, hit rate, cost."""

import csv
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
PRICES = {"alpha": 0.02, "bravo": 0.05, "charlie": 0.01}  # card list prices: bravo is the priciest


def write_cards(tmp_path: Path, prices: dict[str, float] = PRICES) -> Path:
    """Agent cards for the fixture providers, in `tmp_path/cards/` (one JSON file per card)."""
    cards = tmp_path / "cards"
    cards.mkdir(exist_ok=True)
    for slug, price in prices.items():
        card = {
            "slug": slug,
            "name": slug.capitalize(),
            "description": "Extracts invoice fields to JSON.",
            "version": "0.1.0",
            "job_types": ["invoice_extraction"],
            "price_per_doc_usd": price,
            "region": "global",
        }
        (cards / f"{slug}.json").write_text(json.dumps(card))
    return cards


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
    """One tuning job j01: in the trial alpha@0.1.0 scores 1.0 and the others 0.0 (or time out).
    Also writes the providers' agent cards to `tmp_path/cards/` (see `write_cards`)."""
    write_cards(tmp_path)
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
    [r] = an.analyse(["t"], runs, manifest, tmp_path / "cards")

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
    assert [p.provider for p in r.picks["most_expensive"]] == ["bravo@0.1.0"]
    assert r.sq("most_expensive") == pytest.approx(0.6 / 0.9)

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
    [r] = an.analyse(["t"], runs, manifest, tmp_path / "cards")
    assert r.picks["tth@12"][0].provider is None
    assert r.sq("tth") == 0.0
    assert an.aggregate([r], "tth", "tuning").bad_picks == 1


def test_main_writes_tables_and_charts(tmp_path):
    runs, manifest = write_run(tmp_path)
    out = tmp_path / "results"
    argv = ["--runs", "t", "--runs-dir", str(runs), "--manifest", str(manifest), "--out", str(out)]
    argv += ["--cards-dir", str(tmp_path / "cards")]
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
    assert "| Most expensive (exploratory) | 1 | 0.667" in table
    assert "Claim SUPPORTED" in table  # the exploratory row doesn't enter the §7 verdict
    main_csv = (out / "main_table.csv").read_text()
    assert "tuning,Most expensive (exploratory),1,0.666667" in main_csv
    per_job = (out / "per_job.csv").read_text().splitlines()
    assert "sq_most_expensive" in per_job[0].split(",")
    assert "| random | most_expensive | ties @6 |" in (out / "report.md").read_text()


def test_unknown_run_is_a_clear_error(tmp_path):
    runs, manifest = write_run(tmp_path)
    argv = ["--runs", "nope", "--runs-dir", str(runs), "--manifest", str(manifest)]
    assert an.main(argv) == 1


@pytest.mark.parametrize("change", ["off", "on"])
def test_change_flag_sets_the_env_var(tmp_path, monkeypatch, change):
    monkeypatch.delenv(an.CHANGE_ENV, raising=False)
    runs, manifest = write_run(tmp_path)
    argv = ["--runs", "t", "--runs-dir", str(runs), "--manifest", str(manifest)]
    argv += ["--cards-dir", str(tmp_path / "cards")]
    argv += ["--out", str(tmp_path / "out"), "--no-charts", "--change", change]
    assert an.main(argv) == 0
    assert os.environ[an.CHANGE_ENV] == change
    assert ("Not judged" in (tmp_path / "out" / "main_table.md").read_text()) == (change == "on")


def test_load_cards_reads_the_seven_original_cards():
    cards = an.load_cards()
    assert len(cards) == 7
    assert [c.slug for c in cards] == sorted(c.slug for c in cards)
    assert an.load_cards(an.CARDS_DIR) == cards
    assert an.most_expensive_provider(cards) == "quillbase@0.1.0"


def test_load_cards_on_an_empty_dir_is_a_clear_error(tmp_path):
    with pytest.raises(an.AnalysisError):
        an.load_cards(tmp_path)


def test_most_expensive_without_heldout_answers_is_a_clear_error(tmp_path):
    runs, manifest = write_run(tmp_path)
    write_cards(tmp_path, {**PRICES, "delta": 0.09})  # priciest card, but no logged answers
    with pytest.raises(an.AnalysisError, match="delta@0.1.0"):
        an.analyse(["t"], runs, manifest, tmp_path / "cards")


def test_most_expensive_is_left_out_without_cards(tmp_path):
    runs, manifest = write_run(tmp_path)
    [job] = an.load(["t"], runs, manifest)
    r = an.analyse_job(job)
    assert "most_expensive" not in r.picks
    assert all(row.method != "Most expensive (exploratory)" for row in an.main_rows([r]))


# --- card variants (AGE-63) -------------------------------------------------------------------

SHUFFLED = {"alpha": 0.05, "bravo": 0.01, "charlie": 0.02}  # alpha priciest, bravo cheapest


def write_variant_cards(tmp_path: Path, variant: str, prices: dict[str, float]) -> Path:
    """Fixture cards for one variant in `tmp_path/vcards/<variant>/`; returns `tmp_path/vcards`."""
    stage = tmp_path / f"_stage_{variant}"
    stage.mkdir()
    target = tmp_path / "vcards" / variant
    target.parent.mkdir(exist_ok=True)
    write_cards(stage, prices).rename(target)
    return target.parent


def write_variant_run(tmp_path: Path, picks: dict[str, str], jobs=("j01",)) -> None:
    """A profile_variants run `v` in `tmp_path/runs/`: 5 picks per job of `picks[variant]`."""
    run = tmp_path / "runs" / "v"
    run.mkdir(parents=True)
    rows = [
        _pick(job, f"profile:{variant}", k, picked, selection_cost_usd=0.0, selection_time_ms=10)
        for variant, picked in picks.items()
        for job in jobs
        for k in range(1, 6)
    ]
    (run / "picks.jsonl").write_text("\n".join(json.dumps(p) for p in rows) + "\n")


def _variant_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    runs, manifest = write_run(tmp_path)
    write_variant_cards(tmp_path, "original", PRICES)
    root = write_variant_cards(tmp_path, "price_shuffled", SHUFFLED)
    write_variant_run(tmp_path, {"original": "bravo@0.1.0", "price_shuffled": "alpha@0.1.0"})
    return runs, manifest, root


def test_variants_give_one_row_per_method_and_variant(tmp_path, monkeypatch):
    monkeypatch.setenv(an.CHANGE_ENV, "off")
    runs, manifest, root = _variant_fixture(tmp_path)
    rows = an.analyse_variants("v", ["t"], runs, manifest, root)
    assert os.environ[an.CHANGE_ENV] == "off"  # restored after the change-on re-ranking

    got = {(r.method, r.variant): r for r in rows}
    assert [(r.method, r.variant) for r in rows] == [
        (m, v) for m in an.VARIANT_METHODS for v in ("original", "price_shuffled")
    ]
    sq = {k: r.sq for k, r in got.items()}
    assert sq == pytest.approx(
        {
            ("profile", "original"): 0.6 / 0.9,
            ("profile", "price_shuffled"): 1.0,
            ("cheapest", "original"): 0.3 / 0.9,
            ("cheapest", "price_shuffled"): 0.6 / 0.9,
            ("most_expensive", "original"): 0.6 / 0.9,
            ("most_expensive", "price_shuffled"): 1.0,
            ("tth", "original"): 1.0,
            ("tth", "price_shuffled"): 1.0,
        }
    )
    assert got[("cheapest", "price_shuffled")].picked == "bravo@0.1.0"
    assert got[("most_expensive", "price_shuffled")].picked == "alpha@0.1.0"
    assert got[("profile", "original")].picked == "bravo@0.1.0 ×5"
    prof = got[("profile", "original")]
    assert (prof.worst_sq, prof.worst_job, prof.bad_picks, prof.failed_runs) == (
        pytest.approx(0.6 / 0.9),
        "j01",
        1,
        0,
    )
    tth = got[("tth", "original")]
    assert (tth.correct, tth.n_invoices, tth.bad_picks) == (2, 2, 0)  # alpha: both ≥ 0.90


def test_main_variants_writes_md_and_csv(tmp_path):
    runs, manifest, root = _variant_fixture(tmp_path)
    out = tmp_path / "card-variants"
    argv = ["--variants", "v", "--runs", "t", "--runs-dir", str(runs), "--manifest", str(manifest)]
    argv += ["--cards-dir", str(tmp_path / "cards"), "--cards-root", str(root), "--out", str(out)]
    assert an.main(argv) == 0
    assert sorted(p.name for p in out.iterdir()) == ["variants.csv", "variants.md"]
    md = (out / "variants.md").read_text()
    assert "exploratory, not pre-registered" in md
    assert "| Cheapest | price_shuffled | 1 | 0.667 |" in md
    text = (out / "variants.csv").read_text()
    assert len(text.splitlines()) == 1 + 4 * 2
    assert '"Trial-then-Hire (6), change on",original,1,1.0,' in text
    assert not (tmp_path / "results").exists()  # the main tables aren't written


def test_variants_must_cover_every_ground_truth_job(tmp_path):
    runs, manifest = write_run(tmp_path)
    root = write_variant_cards(tmp_path, "original", PRICES)
    write_variant_run(tmp_path, {"original": "bravo@0.1.0"}, jobs=("j02",))
    with pytest.raises(an.AnalysisError, match="missing"):
        an.analyse_variants("v", ["t"], runs, manifest, root)


def test_unknown_variant_in_picks_is_a_clear_error(tmp_path):
    runs, manifest = write_run(tmp_path)
    root = write_variant_cards(tmp_path, "original", PRICES)
    write_variant_run(tmp_path, {"bogus": "bravo@0.1.0"})
    with pytest.raises(an.AnalysisError, match="bogus"):
        an.analyse_variants("v", ["t"], runs, manifest, root)


def test_variants_main_on_a_missing_run_is_a_clear_error(tmp_path):
    runs, manifest = write_run(tmp_path)
    argv = ["--variants", "nope", "--runs", "t", "--runs-dir", str(runs)]
    argv += ["--manifest", str(manifest), "--out", str(tmp_path / "o")]
    assert an.main(argv) == 1


def test_main_without_runs_or_variants_is_a_usage_error():
    with pytest.raises(SystemExit):
        an.main([])


def test_variants_on_the_logged_runs_match_age62(tmp_path, monkeypatch):
    """End to end on the mock: profile_variants --mock into a temp run, then analyze --variants
    against the real tuning-r1 / fresh-r1 held-out answers and the committed card variants."""
    import socket

    from experiments.hw7 import profile_variants as pv
    from tests.test_hw7_profile_variants import write_jobs

    def refuse(*args, **kwargs):
        raise AssertionError("network access in an offline test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    runs = tmp_path / "runs"
    runs.mkdir()
    for run in an.VARIANT_GT_RUNS:
        (runs / run).symlink_to(an.RUNS_DIR / run)
    jobs = write_jobs(tmp_path)
    for variant in ("original", "inflated"):
        opts = pv.VariantOptions(variant=variant, run_id="test-mock", mock=True)
        assert pv.run_variant(opts, jobs_dir=jobs, runs_dir=runs)[0] == 0

    out = tmp_path / "card-variants"
    assert an.main(["--variants", "test-mock", "--runs-dir", str(runs), "--out", str(out)]) == 0
    with (out / "variants.csv").open() as f:
        rows = {(r["method"], r["variant"]): r for r in csv.DictReader(f)}
    assert len(rows) == 4 * 2
    assert round(float(rows[("Cheapest", "original")]["mean_sq"]), 3) == 0.658
    assert round(float(rows[("Most expensive (exploratory)", "original")]["mean_sq"]), 3) == 0.998
    assert rows[("Most expensive (exploratory)", "original")]["hired"] == "quillbase@0.1.0"
    tth = [r for (m, _), r in rows.items() if m.startswith("Trial-then-Hire")]
    assert len(tth) == 2 and {round(float(r["mean_sq"]), 3) for r in tth} == {0.947}
    assert {r["n_invoices"] for r in rows.values()} == {"144"}
    assert {r["n_jobs"] for r in rows.values()} == {"12"}
    assert not (an.RUNS_DIR / "test-mock").exists()
