# Integration: 2026-11-18-age-62-63

Base: `origin/main` @ `5574224`. Integration branch: `integration/2026-11-18-age-62-63`.

## Merge order
1. `linear/AGE-62` @ `8874184` → merge `2f9c46e` (no conflicts)
2. `linear/AGE-63` @ `dcc7cb7` (branched from `2f9c46e`) → merge `1d568b6` (no conflicts)

## Per-issue status
| Issue | Status | Notes |
|---|---|---|
| AGE-62 | DONE | Most-expensive baseline, `load_cards`, results regenerated; only added rows/columns in results diff. Added `--cards-dir`. |
| AGE-63 | DONE | Card variant generator + committed variants, profile-variant runner (mock, resumable, spend cap), `analyze --variants`. `results/card-variants/` intentionally empty until AGE-65. |

## Conflict resolutions
None (sequential lane, no conflicts).

## Gate results (on integration tip after both merges)
| Check | Result |
|---|---|
| `uv run pytest` | 605 passed, 15 skipped, 5 deselected (live) |
| `uv run ruff check` | All checks passed |
| `uv run ruff format --check` | 129 files already formatted |
| `card_variants` rerun | no diff (deterministic) |
| Variant spot-check | inflated: 7 cards, 1 shared description; both variants keep the original price multiset |
| Main results | `change-on/main_table.csv` only adds rows; all/most-expensive SQ 0.998412, bad picks 0; verdict "Claim NOT SUPPORTED" |
| Untouched | `run_benchmark.py`, `PREREG.md`, `runs/`, `cards/original/`, main results (by AGE-63) |

## Pre-existing failures
None.

## Contract gaps raised
- AGE-63: run_benchmark's platform-account check is inline, not importable; `profile_variants.py` repeats the one comparison using imported `PLATFORM_ACCOUNT` / `HarnessError` rather than changing run_benchmark.

## Non-additive edits to note
- AGE-63 made `--runs` optional in `analyze.py` (defaults to `tuning-r1 fresh-r1` with `--variants`; still a usage error without it). Added `--cards-root`.
- AGE-63 imports `Harness._pick` / `Harness.check_spend` from run_benchmark (private-ish method reuse, per "import, don't copy").

## Open questions / for AGE-65
- Seed-0 shuffle did not leave Quillbase highest (documented in `cards/README.md`).
- `jobs/j02.json` and `j09.json` (DocILE) are gitignored; they exist in the main checkout, so the live run must run there.
