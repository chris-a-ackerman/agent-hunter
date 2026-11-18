# Handoff: AGE-66 — Make accuracy-first tie-breaking the default ranking rule

- Branch: linear/AGE-66 @ 0771daf (code + docs; this handoff commit sits on top)
- Base: integration/2026-11-18-age-66-68 @ 35d148b
- Status: DONE
- UI-touching: no
- Tests: `uv run pytest` → 580 passed, 15 skipped, 5 deselected (base: 576 passed)
- Tests: `uv run ruff check` → All checks passed
- Tests: `uv run ruff format --check` → 123 files already formatted
- HW7: `uv run python -m experiments.hw7.analyze --runs tuning-r1 fresh-r1` and `... --change on` → no diff on any file in `experiments/hw7/results/` (including both `main_table.csv` files); no regenerated output needed restoring

## What changed
`accuracy_first()` now returns True when `HW7_CHANGE` is unset or empty, as well as when it is `on`. `off` restores the old cost-first tie-break, and any other value still raises `ValueError`. It is still read on every call. So by default a tie's `recommended` is the tied provider with the highest trial accuracy (exact accuracy ties: cost per doc, then latency, then provider ID). Tie detection and the `tied` list are unchanged. Docstrings (stats module, `accuracy_first`, `decide`, `Verdict`), the exported `Verdict.json`, PLAN.md D24 and one README line describe the new default.

## Files touched
- core/eval/stats.py — default flips to "on"; module, `accuracy_first()` and `decide()` docstrings
- contracts/src/contracts/verdict.py — `tie` docstring wording from the contract
- contracts/schemas/Verdict.json — regenerated with `uv run python -m contracts.export`; only the `description` string changed (no field changes)
- PLAN.md — D24 tie wording, dated Nov 18, "AGE-39 made default, AGE-66", notes `HW7_CHANGE=off`
- README.md — one **Tie-break** paragraph after Setup
- tests/test_stats.py — unset/empty/whitespace/on/ON → trial leader (and `accuracy_first() is True`); off/OFF → cheapest; `yes`/`maybe` → ValueError; the two old cost-tie-break tests now pin `HW7_CHANGE=off` (renamed `..._with_change_off`)
- tests/test_ranker.py — `test_tie_path_is_valid_verdict` expects the more accurate provider with the env unset; new test: env unset → rationale "wins it on trial accuracy, then cost per document", `off` → "wins it on cost per document, then latency"; `on` gives the same Verdict as unset

## Assumptions
- PLAN.md D24's date column stays "Oct 23" (the original decision date). The Nov 18 date and the AGE-39/AGE-66 note go inline in the D24 text.
- The `experiments/hw7/analyze.py` comment "read by the AGE-39 change; default off" refers to analyze's own `--change` flag, whose default is still `off`, so I left it alone (not in my file list).

## Contract gaps
- None.

## Left undone
- None. `core/agents/ranker.py` is unchanged (no test required a change). No other code or conftest assumed the old default; `tests/test_hw7_analyze.py` sets the env explicitly and passes unchanged.

## Merge notes
- README.md: I added one paragraph right after **Setup**. AGE-67/68 edit only the demo web app section, so I expect no overlap.
- All other files are owned by AGE-66 per the contract.
- The PR needs the `contract-change` label and Nathalie's approval (Verdict docstring + schema).

## How to verify
- `uv run pytest tests/test_stats.py tests/test_ranker.py -q`
- `uv run pytest && uv run ruff check && uv run ruff format --check`
- `uv run python -m experiments.hw7.analyze --runs tuning-r1 fresh-r1` and `... --change on`, then `git status --porcelain experiments/hw7/results` is empty
- `git diff 35d148b -- contracts/schemas/Verdict.json` shows only the `description` line
