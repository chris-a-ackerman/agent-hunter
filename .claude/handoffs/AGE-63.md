# Handoff: AGE-63 — Build the misleading-card experiment for profile-reading and price baselines

- Branch: linear/AGE-63 @ 1a5b71e (code; this handoff commit sits on top)
- Base: integration/2026-11-18-age-62-63 @ 2f9c46e
- Status: DONE
- UI-touching: no
- Tests: `uv run pytest tests/test_hw7_analyze.py tests/test_hw7_profile_variants.py tests/test_hw7_card_variants.py` → 34 passed
- Tests: `uv run pytest` → 605 passed, 15 skipped, 5 deselected (live)
- Tests: `uv run ruff check` → all checks passed; `uv run ruff format --check` → 128 files already formatted

## What changed
There are three new pieces, all offline (mock only, no model calls):
1. `card_variants.py` builds `cards/inflated/` (every card gets Quillbase's description and keeps
   its own price) and `cards/price_shuffled/` (prices permuted with `random.Random(0).shuffle` over
   the slug-sorted list) from `cards/original/` only. The JSON is generated and committed. With
   seed 0, Quillbase does **not** keep the highest price: it gets the lowest (0.002) and Docuvant
   gets the highest (0.06). `cards/README.md` says so and shows the full permutation. It wasn't re-rolled.
2. `profile_variants.py` runs `pick_by_profile` n times per job on a variant's cards. It logs
   run_benchmark-format profile rows with `method: "profile:<variant>"` plus `llm_calls.jsonl`, and it resumes.
   It reuses `RunLog`, `RunGateway`, `MockGateway`, `Harness.check_spend` / `Harness._pick`,
   `SpendCapReached`, `select_jobs`, `git_commit` and `PLATFORM_ACCOUNT` from `run_benchmark.py`, all imported.
3. `analyze --variants <run_id>` writes `results/card-variants/variants.md` and `.csv`, with one row
   per method × variant present. The metrics are mean SQ (with a CI), worst-job SQ, bad picks, invoices
   correct out of 144, failed runs and who got hired. The main-table path is unchanged.

Mock end-to-end check on the logged tuning-r1 + fresh-r1 data: Cheapest/original 0.658 and
Most expensive/original 0.998 (`quillbase@0.1.0`), matching AGE-62. Trial-then-Hire (6, change on)
is 0.947 for every variant. Under price_shuffled, Cheapest becomes `quillbase@0.1.0` (0.998) and Most
expensive becomes `docuvant@0.1.0`.

## Files touched
- experiments/hw7/card_variants.py — new: the generator (deterministic and mechanical; removes stale files)
- experiments/hw7/cards/inflated/*.json, experiments/hw7/cards/price_shuffled/*.json — new, generated
- experiments/hw7/cards/README.md — describes the variants, the seed and the resulting permutation
- experiments/hw7/profile_variants.py — new: the runner (CLI `--variant --jobs --n --run-id --mock --max-spend-usd --platform-account`)
- experiments/hw7/analyze.py — additive: `CARDS_ROOT`, `VARIANTS_OUT`, `VARIANT_GT_RUNS`, `VARIANT_METHODS`,
  `VariantRow`, `load_variant_picks`, `analyse_variants`, `render_variants`, `write_variants`;
  `--variants` and `--cards-root` options right after `--cards-dir` in `main()`; `pick_cheapest` import; docstring lines
- tests/test_hw7_card_variants.py — new: determinism, committed files equal the generator output, inflated/shuffled invariants, seed
- tests/test_hw7_profile_variants.py — new: 60 mock picks offline, resume, row keys, spend cap, missing posting, account check, CLI
- tests/test_hw7_analyze.py — new tests only (+ `import csv`): fixture variant rows, md/csv writer, coverage errors, real-data match to AGE-62

## Assumptions
- `analyze.py --runs` is no longer `required=True`. With `--variants` it defaults to `tuning-r1 fresh-r1`,
  and without `--variants` a missing `--runs` is still a usage error (`ap.error`). This is the only
  non-additive line in analyze.py besides the import.
- Variants mode always re-ranks Trial-then-Hire with `HW7_CHANGE=on`, whatever `--change` says, and restores the
  env var afterwards. `--change` is ignored there.
- Added `--cards-root` (default `cards/`), so tests can point at fixture `cards/<variant>/` dirs.
- `analyse_variants` is strict. Each variant present must have picks for exactly the ground-truth jobs, and
  every hired provider must have held-out answers. Otherwise it raises `AnalysisError`.
- Invoices correct is an expected value: profile-reading averages its runs per job, and Trial-then-Hire averages
  its repeats × draws. So it can be fractional (e.g. 120.6/144).
- Mock profile picks answer the first slug-sorted card (`docuvant`), the same way run_benchmark's mock answers
  `registry[0]`.
- The spend cap is checked before each pick. One pick can retry up to 3 calls, so a pick that starts under the
  cap can end a few cents over it, the same as run_benchmark.
- A live run also checks the platform account. `--platform-account` (default `nathalie`) mirrors run_benchmark,
  so AGE-65 can choose the account.
- Variant job IDs follow the contract (`hw7-j01-vinflated-p1`), so they count toward `RunLog.platform_spend()` (prefix `hw7-`).
- No mock results are committed under `results/card-variants/`. Those numbers would come from a mock profile
  pick. AGE-65's live run fills that directory.

## Contract gaps
- run_benchmark's platform-account check is inline in `run_benchmark()`, not a function, so it can't be
  imported without changing that file. `profile_variants._platform_gateway` repeats the comparison (same
  message, imported `PLATFORM_ACCOUNT` / `HarnessError`). Extracting a shared helper later would remove the copy.

## Left undone
- The live run (AGE-65). The DocILE postings (`jobs/j02.json`, `jobs/j09.json`) are gitignored. In a checkout
  without them, `profile_variants` stops with a clear "postings missing for j02, j09" error before writing anything.
  The live run needs them in place (`make_jobs` with DocILE).

## Merge notes
- experiments/hw7/analyze.py: everything new sits in its own section ("card variants (AGE-63)") just above
  `# --- CLI`, plus two new options after `--cards-dir`. AGE-62's code is untouched apart from `--runs`
  losing `required=True`.
- tests/test_hw7_analyze.py: appended at the end. The only other edit is `import csv` at the top.
- results/ (main + change-on) and runs/ weren't touched.

## How to verify
```bash
uv run pytest tests/test_hw7_card_variants.py tests/test_hw7_profile_variants.py tests/test_hw7_analyze.py
uv run python -m experiments.hw7.card_variants && git status --porcelain experiments/hw7/cards   # empty: byte-identical
# full mock end to end without touching runs/ (uses a temp dir; j02/j09 get j01's posting as a stand-in):
uv run pytest tests/test_hw7_analyze.py::test_variants_on_the_logged_runs_match_age62 -v
# with the DocILE postings present (main checkout), the real CLI:
#   uv run python -m experiments.hw7.profile_variants --mock --variant inflated --run-id test-mock
#   uv run python -m experiments.hw7.analyze --variants test-mock --out /tmp/cv && rm -r experiments/hw7/runs/test-mock
```
