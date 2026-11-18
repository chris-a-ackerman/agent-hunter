# Batch contract

Workers follow this exactly and never edit it. If you need something not covered here, record a
"Contract gap" in your handoff instead of inventing it.

- Batch: `2026-11-18-age-66-68`
- Base branch: `integration/2026-11-18-age-66-68` (cut from `origin/main` @ `5574224`)
- Issues: `AGE-66, AGE-67, AGE-68`: one lane, strictly sequential. AGE-67 branches after AGE-66 is merged; AGE-68 after AGE-67.
- Filled in by orchestrator on: `2026-11-18`
- Repo rules: `CLAUDE.md` (agent-hunter). No DB, no alembic, no frontend build: `apps/web/index.html` is one page, no framework.
- Gate ("tests pass"): `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`. Live tests stay skipped. Browser checks are done by the orchestrator at integration; flag `UI-touching: yes` and list exactly what to check in "How to verify".

## 1. Shared interfaces / types

| Name | Location | Shape / signature | Owner issue | Consumers |
|---|---|---|---|---|
| `accuracy_first()` | `core/eval/stats.py` | `accuracy_first() -> bool`. `HW7_CHANGE` unset or empty → `True`; `off` → `False`; `on` → `True`; anything else → `ValueError`. Read on every call (no caching). Single source of truth for the tie-break rule. | AGE-66 | AGE-67 (via `core.web`), `core/agents/ranker.py` (unchanged) |
| `Verdict` docstring for `tie` | `contracts/src/contracts/verdict.py`, `contracts/schemas/Verdict.json` | "`recommended` = the tied provider with the highest trial accuracy; exact accuracy ties broken by cost per doc, then latency". Regenerate the schema with `contracts/src/contracts/export.py`; docstring-only, no field changes. | AGE-66 | — |
| `core.web --offline` / `AGENT_HUNTER_OFFLINE=1` | `core/web.py` | Offline wiring reuses `core.a2a.offline_build_steps` (`core/a2a.py:98`) / `core.pipeline.stubs.offline_steps` and `core.a2a.OFFLINE_STORE_DIR` (`.data/offline-store`); import, don't copy. `create_app(...)` must accept an offline option so tests can build it without the CLI. | AGE-67 | AGE-68 |
| Run view `tie_break` | `GET /api/config` and `GET /api/runs/{job_id}` | `"accuracy_first" \| "cost"`, from `accuracy_first()` | AGE-67 | AGE-68 |
| Run view `mode` | `GET /api/config` and `GET /api/runs/{job_id}` | `"live" \| "offline"` | AGE-67 | AGE-68 |
| Candidates in run view | `GET /api/runs/{job_id}` | Includes A2A-listed agents with their listing source and exclusion reason (as the A2A hire result reports them, AGE-59/60) | AGE-67 | AGE-68 (replays show it) |
| Timeline format | `<STORE_DIR>/web_runs/<job_id>.jsonl` (one per line), `apps/web/replays/<job_id>.json` (JSON array) | `{"t": float elapsed_s, "view": <run view>}` | AGE-68 | — |
| Replay routes | `core/web.py` | `GET /api/replays` → list of `{job_id, example, mode, created_at, duration_s, outcome}`; `GET /api/replays/{job_id}` → snapshots oldest first, 404 if missing | AGE-68 | — |
| `--export-replay <job_id>` | `core/web.py` CLI | writes `apps/web/replays/<job_id>.json` | AGE-68 | — |
| URL params | `apps/web/index.html` | `?demo` (existing fixture), `?replay=<job_id>` or `?replay=replays/<file>.json`, `?speed=` (default 3), `?setup_speed=` (default 8), `?record` | `?demo` fixture refresh: AGE-67; rest: AGE-68 | — |

## 2. Schema / API changes

| Change | Owner issue | Details |
|---|---|---|
| `Verdict` docstring + `contracts/schemas/Verdict.json` | AGE-66 | Docstring only. This is why AGE-66 carries `contract-change`: Nathalie approves the PR. No field changes. |
| `GET /api/config`, `GET /api/runs/{job_id}`: add `tie_break`, `mode` | AGE-67 | additive fields |
| `GET /api/replays`, `GET /api/replays/{job_id}` | AGE-68 | new routes |

No Supabase migrations.

## 3. Naming conventions

- Branches: `linear/<ID>` (workers), `integration/2026-11-18-age-66-68` (integration)
- Commits: `<type>(<area>): <summary> (<ID>)`
- Handoffs: `.claude/handoffs/<ID>.md`, integration log `.claude/handoffs/INTEGRATION.md`
- Test DB: none
- Page badges: "Offline · stub agents" (AGE-67), "Replay of a real run · sped up" + original run date (AGE-68)
- Tie wording: "tie broken on trial accuracy" / "tie broken on cost" (AGE-67). Ranker template rationale: "wins it on trial accuracy, then cost per document" (existing text; AGE-66 tests it with env unset)

## 4. Decisions already made

- Accuracy-first becomes the default; `HW7_CHANGE=off` restores cost-first. Tie detection (CI overlap) and the `tied` list don't change. `core/agents/ranker.py` already reads `accuracy_first()`; only fix tests that assumed the old default. (AGE-66)
- AGE-66 edits `core/eval/` and Ranker tests because the issue asks it to (precedent: AGE-39, commit 081a1c9). Keep that edit to exactly the scope above. (AGE-66, CLAUDE.md workstreams)
- The HW7 analysis output must not change: `analyze.py --change off|on` sets the env var explicitly. Re-run `uv run python -m experiments.hw7.analyze --runs tuning-r1 fresh-r1` (and `--change on`) and confirm `git diff` on the `main_table.csv` files is empty; then restore any regenerated files you didn't mean to change. (AGE-66)
- `PLAN.md` D24 is edited by AGE-66 only, as the issue says. (AGE-66, CLAUDE.md "Don't edit PLAN.md unless the issue asks")
- Keep one page and the polling API: no framework, no build step. No visual redesign. No pipeline behavior changes in AGE-67/68. (AGE-67, AGE-68, Nov 18)
- The `?demo` fixture stays anonymized placeholder data and is never presented as a real run. (AGE-67)
- Replay plays a real recorded run, never edits snapshots; the "sped up" badge is always on screen and timers show real recorded seconds. (AGE-68, Nov 18)
- No live model calls anywhere in this batch. Tests use offline mode / mocks; no `.env`; no network beyond localhost. (all)
- Hidden-traits rule (CLAUDE.md): no provider trait in code, comments, tests, fixtures, commits or handoffs. Never read `../agent-hunter-providers/`.
- Timeline writes must never fail a run: catch, log a warning, continue. (AGE-68)

## 5. File ownership (contention)

| File | Owner issue | Others touching it |
|---|---|---|
| `core/eval/stats.py`, `contracts/src/contracts/verdict.py`, `contracts/schemas/Verdict.json`, `PLAN.md`, `tests/test_stats.py`, `tests/test_ranker.py` | AGE-66 | none |
| `core/web.py` | AGE-67 (offline mode, view fields, candidates) | AGE-68: additive (timeline capture, replay routes, `--export-replay`); don't restructure AGE-67's code |
| `apps/web/index.html` | AGE-67 (audit fixes, tie wording, offline badge, `?demo` fixture) | AGE-68: additive (replay clock, `?replay`, `?record` layout) |
| `tests/test_web.py` | AGE-67 | AGE-68: add tests only |
| `apps/web/AUDIT.md` | AGE-67 | AGE-68 may append a replay row only if needed |
| `README.md` | AGE-66 (one line on the tie-break default) | AGE-67, AGE-68: demo web app section only, additive |
