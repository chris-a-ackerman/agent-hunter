# Batch contract

Workers follow this exactly and never edit it. If you need something not covered here, record a
"Contract gap" in your handoff instead of inventing it.

- Batch: `2026-11-18-age-62-63`
- Base branch: `integration/2026-11-18-age-62-63` (cut from `origin/main` @ `5574224`)
- Issues: `AGE-62, AGE-63` (one lane, sequential: AGE-62 first, AGE-63 branches after AGE-62 is merged)
- Filled in by orchestrator on: `2026-11-18`
- Repo rules: `CLAUDE.md` (agent-hunter). No backend DB, no frontend, no alembic in this repo.
- Gate ("tests pass"): `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`. Live tests stay skipped.

## 1. Shared interfaces / types

| Name | Location | Shape / signature | Owner issue | Consumers |
|---|---|---|---|---|
| `pick_most_expensive` | `experiments/hw7/baselines.py` (next to `pick_cheapest`) | `pick_most_expensive(cards: list[AgentCard]) -> str`: slug with highest `price_per_doc_usd`; ties broken by slug; `ValueError` on empty list (like `pick_cheapest`) | AGE-62 | AGE-63 |
| `load_cards` | `experiments/hw7/analyze.py` | `load_cards(cards_dir: Path) -> list[AgentCard]`: one card per `*.json`, sorted by slug; default dir `experiments/hw7/cards/original` | AGE-62 | AGE-63 |
| Provider ID format | analysis | `f"{card.slug}@{card.version}"`, e.g. `quillbase@0.1.0` | existing | both |
| `most_expensive` method | `experiments/hw7/analyze.py` `METHODS` / `METHOD_LABEL` | key `most_expensive`, label `"Most expensive (exploratory)"`, selection cost $0 | AGE-62 | AGE-63 |
| Pick `method` values | `runs/<run_id>/picks.jsonl` | `profile:original`, `profile:inflated`, `profile:price_shuffled`; same row format as `run_benchmark` profile picks | AGE-63 | AGE-65 (later, not in batch) |
| Variant outputs | `experiments/hw7/results/card-variants/` | `variants.md`, `variants.csv` | AGE-63 | AGE-65 (later) |
| Existing (don't change) | `experiments/hw7/baselines.py`, `experiments/hw7/run_benchmark.py` | `pick_by_profile(posting, cards, *, gateway, job_id) -> ProfilePick`, `pick_cheapest(cards) -> str`, `RunGateway`, `MockGateway`, `SpendCapReached`, platform-account check | existing | AGE-63 imports them; never copies them |

## 2. Schema / API changes

None. No Supabase migrations, no `contracts/` package changes, no routes.

## 3. Naming conventions

- Branches: `linear/<ID>` (workers), `integration/2026-11-18-age-62-63` (integration)
- Commits: `<type>(<area>): <summary> (<ID>)`, e.g. `feat(hw7): most-expensive baseline (AGE-62)`
- Handoffs: `.claude/handoffs/<ID>.md`, integration log `.claude/handoffs/INTEGRATION.md`
- Test DB: none
- Variant names: `original`, `inflated`, `price_shuffled`; card dirs `experiments/hw7/cards/<variant>/`
- `job_id` for variant profile picks: `f"hw7-{job}-v{variant}-p{k}"`

## 4. Decisions already made

- `most_expensive` is exploratory, labelled so, and NOT part of the pre-registered pass rule (PREREG §7). The verdict line stays "Claim NOT SUPPORTED". (AGE-62; Nov 18 slide-4 review)
- **Fairness rule (PREREG §13): never read files outside this repo.** Never open `../agent-hunter-providers/` or any `TRAITS.md`. Card data comes only from `experiments/hw7/cards/original/`. (AGE-62, AGE-63)
- Hidden-traits rule (CLAUDE.md): no provider trait may appear in code, comments, tests, commits or handoffs.
- Card variants are generated mechanically; nobody hand-edits them. `price_shuffled` uses `random.Random(0).shuffle` over the slug-sorted list; if Quillbase stays highest, keep it and say so in `cards/README.md`; don't re-roll. (AGE-63)
- No live model calls and no new benchmark runs in this batch. AGE-63's tests and end-to-end check use `--mock` only. (AGE-62, AGE-63)
- Don't edit `experiments/hw7/PREREG.md`, `experiments/hw7/runs/tuning-r1/`, `experiments/hw7/runs/fresh-r1/`, or `experiments/hw7/cards/original/`. AGE-63 does not regenerate the main results tables. (AGE-62, AGE-63)
- AGE-62 regenerates results with exactly `uv run python -m experiments.hw7.analyze --runs tuning-r1 fresh-r1` and the same with `--change on`. Existing rows must be unchanged (change on, all: Trial-then-Hire 0.947, Profile-reading 0.907). (AGE-62)
- 5 profile picks per job per variant. Variant results are exploratory and say so in `variants.md`. (AGE-63)
- Mock test run IDs (e.g. `test-mock`) must write to a temp dir in tests, not leave run folders committed under `experiments/hw7/runs/`. (orchestrator)

## 5. File ownership (contention)

| File | Owner issue | Others touching it |
|---|---|---|
| `experiments/hw7/analyze.py` | AGE-62 (method plumbing, `load_cards`) | AGE-63: additive only (`--variants` CLI option + new variants writer); don't restructure AGE-62's code |
| `tests/test_hw7_analyze.py` | AGE-62 | AGE-63: add new tests only |
| `experiments/hw7/baselines.py` | AGE-62 | AGE-63 imports only |
| `experiments/hw7/cards/README.md` | AGE-63 | — |
| `experiments/hw7/results/` (main + `change-on/`) | AGE-62 | AGE-63 must not regenerate; writes only `results/card-variants/` |
| `experiments/hw7/run_benchmark.py` | nobody (no behaviour change) | AGE-63 imports only |
