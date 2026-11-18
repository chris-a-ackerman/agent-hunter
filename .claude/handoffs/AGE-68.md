# Handoff: AGE-68 — Replay mode and recording layout for the 30-second demo

- Branch: linear/AGE-68 @ 153cc22 (code + docs; this handoff commit sits on top)
- Base: integration/2026-11-18-age-66-68 @ 130fb07
- Status: DONE
- UI-touching: yes
- Tests: `uv run pytest` → 595 passed, 15 skipped, 5 deselected (base: 590 passed)
- Tests: `uv run ruff check` → All checks passed
- Tests: `uv run ruff format --check` → 126 files already formatted
- Tests: node (v26, /opt/homebrew/bin/node): `node --check` on the page's single inline script (existing AGE-67 test), the replay clock harness and a replay page harness that plays a real exported offline inv-mixed timeline (both inside `tests/test_web.py`, skipped if `node` isn't on PATH)

## What changed
- **Timeline capture.** Each `LiveRun` gets `timeline` (path) and `snapshot_view` (set by `create_app`). After every `RunEvent` it handles, and once in `end()`, it appends `{"t": elapsed_s, "view": <run view>}` to `<STORE_DIR>/web_runs/<job_id>.jsonl`. The append runs in the pipeline thread, under the run's lock. `Lock` became `RLock`, so the snapshot can build the view under the same lock. Any exception logs `timeline write failed for <job> (...)` and the run continues.
- **STORE_DIR resolution.** `timeline_dir(store, offline=)` uses the store's own `root` if it has one, then `STORE_DIR`, then `.data/offline-store` offline (`core.a2a.OFFLINE_STORE_DIR`, imported, not copied) or `.data/store`.
- **Routes.** `GET /api/replays` returns `[{job_id, example, mode, created_at, duration_s, outcome}]`, newest first. `GET /api/replays/{job_id}` returns the snapshots oldest first, or 404. A malformed id also gets 404. A partial line is skipped with a warning. There is one supporting route: `GET /replays/{file}.json` serves `apps/web/replays/`, so `?replay=replays/<file>.json` works when the page is served by `core.web`.
- **Export.** `uv run python -m core.web [--offline] --export-replay <job_id>` writes `apps/web/replays/<job_id>.json`: a JSON array, one snapshot per line, unchanged. With STORE_DIR unset it also checks the other mode's default store. It exits non-zero if the timeline isn't found.
- **Run view.** New field `started_at` (ISO, the job record's `created_at`). It gives the badge its date and `/api/replays` its `created_at`.
- **Page.** `?replay=<job_id>` or `?replay=replays/<file>.json` sets `S.mode = 'replay'`, so `isOffline(V)` follows the recorded view's `mode`. The replay clock uses pure functions: `replaySegments`, `replayPlayTime`, `replayRealTime`, `replayFrame` and `replaySource`. Playback runs at `?speed` (default 3) while `trial_runner`/`score` run and at `?setup_speed` (default 8) otherwise. It uses the last snapshot's step timings and holds at the end. `clock()` returns the recorded time in replay, so step timers, lane times and Runtime show real seconds. The "Replay of a real run · sped up" chip, with "run on <date>", sits in a header that is sticky in replay mode. Space plays and pauses (the badge then says "paused"). `r` restarts, as does "Run again". The toggles are disabled in replay.
- **`?record`.** 1920 px wide `#app`. It hides the rail, buttons, theme button, step toggles, the mode chip and the hint. Only the running or failed step and the latest finished step stay open; once the run ends, every step collapses. Lanes and the ranking are enlarged. The existing `follow()` auto-scrolls and ends on the rec card, which stays because the replay holds on the last frame.

## Files touched
- core/web.py — timeline capture in `LiveRun` (additive, plus `Lock`→`RLock`), `started_at` in the view, `timeline_dir`/`read_timeline`/`replay_summary`/`export_replay`, three routes, `--export-replay`, docstring
- apps/web/index.html — replay clock functions, replay state/loading/keys, badge, `?record` CSS and `recordOpen`, small guards (`S.mode` line, `clock`, toggles' `disabled`, hint, `run()`)
- apps/web/replays/.gitkeep — new dir
- tests/test_web.py — added tests only (imports `json`, `JsonStore`, `timeline_dir`)
- README.md — demo web app paragraph, appended

## Assumptions
- The badge's "original run's date" needs data inside the snapshots, because the exported file is just the array. So I added `started_at` to the run view, an additive field on `GET /api/runs/{job_id}` too. See Contract gaps.
- Snapshots are taken on every event the `LiveRun` handles, including `provider_done`, `paused` and `stopped`. That's a superset of "every step start/finish and every task_done".
- The fixture's existing `?speed` (default 1) is unchanged. Replay reads `?speed` with default 3, per contract §1.
- In record mode, "collapses finished steps" keeps the latest finished step open while the run goes on, so the video shows the rubric, trial docs, candidates and scores briefly. Everything collapses when the run ends.
- `?replay` takes precedence over the `file:` → fixture fallback. Opened as a file, the fetch fails and the page shows a "Couldn’t load the replay" banner. Serve the page with `core.web` or any static server rooted at `apps/web/`.
- Replay sorts snapshots by `t` (a stable sort, a no-op for files as written) and never modifies them. The page test asserts they're byte-identical after playing.

## Contract gaps
- Run view field `started_at` (ISO 8601 UTC) is not in contract §1. It's additive and needed for the badge date.
- `GET /replays/{file}.json` (static serving of committed replays) is not in contract §1. It's a support route so `?replay=replays/<file>.json` works from `core.web`.

## Left undone
- No browser checks (no browser tools here). See "How to verify".
- No 1920×1080 auto-zoom: the window must be 1920×1080 (or use devtools' responsive mode at 1920×1080).
- No replay JSON is committed, only `.gitkeep`. Choosing and exporting the demo run is AGE-69's job. `.data/` is already in `.gitignore`.

## Merge notes
- core/web.py: AGE-67's `_candidates`, offline block and `main` offline resolution are untouched. The only modified AGE-67 lines are the `fastapi.responses` import and `threading.Lock()` → `threading.RLock()`.
- apps/web/index.html: modified AGE-67 lines are the `S.mode` initializer (adds a `'replay'` branch first), the toggles' `disabled` condition, `open` in `renderPipeline` (record branch), the `hint` (replay branch), and one guard line each at the top of `clock()`, `run()` and `init()`. Everything else is additions. There is still exactly one inline `<script>`, which `page_script()` requires.
- tests/test_web.py: appended a section at the end plus two import lines.

## How to verify
- `uv run pytest tests/test_web.py -q`, then `uv run pytest && uv run ruff check && uv run ruff format --check`
- Record a run: `STORE_DIR=$(mktemp -d) DOCS_DIR=$(mktemp -d) TRUTH_DIR=$(mktemp -d) uv run python -m core.web --offline --port 8300`. Open http://localhost:8300, run "Invoice extraction (scans, no tax)", then `curl -s localhost:8300/api/replays` (note the `job_id`) and `curl -s localhost:8300/api/replays/<job_id> | head -c 300`.
- Export it with the same `STORE_DIR` and `--offline`: `uv run python -m core.web --offline --export-replay <job_id>`. It writes `apps/web/replays/<job_id>.json`. Don't commit it unless it's the chosen demo run.
- Browser checks at integration:
  1. `http://localhost:8300/?replay=<job_id>`: the run plays sped up. The header chip reads "Replay of a real run · sped up · run on <date>" from the first frame to the last, and stays visible while the page scrolls. The offline chip shows, because the recorded view's mode is offline.
  2. `http://localhost:8300/?replay=replays/<job_id>.json`: same playback from the committed file. Also try a static server: `cd apps/web && python3 -m http.server 8310`, then `http://localhost:8310/?replay=replays/<job_id>.json`.
  3. Step timers, lane finish times and footer Runtime show recorded seconds. They match `/api/runs` durations, not playback time. A real ~87 s run plays in about 22–23 s.
  4. `?replay=<job_id>&record` with the window at 1920×1080 (devtools responsive mode): no rail, buttons, theme toggle or step toggles. Lanes and the ranking are enlarged. The page auto-scrolls to the active step, and only the running step and the latest finished one are expanded. At the end, steps collapse, the ranking shows, and then the recommendation card scrolls into view and stays. The badge stays visible throughout.
  5. Space pauses (the badge shows "paused") and resumes. `r` restarts from step 1.
  6. `?replay=job-nope` shows the "Couldn’t load the replay" banner. The badge still shows.
  7. `?demo` still plays the fixture to the end (header "Fixture playback · placeholder data", no replay badge), and `?demo&moment=rejected` still works.
