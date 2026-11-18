# Handoff: AGE-67 — Audit and refresh the demo web app for the current pipeline

- Branch: linear/AGE-67 @ 363ef89 (code + docs; this handoff commit sits on top)
- Base: integration/2026-11-18-age-66-68 @ 51a473b
- Status: DONE
- UI-touching: yes
- Tests: `uv run pytest` → 590 passed, 15 skipped, 5 deselected (base: 580 passed)
- Tests: `uv run ruff check` → All checks passed
- Tests: `uv run ruff format --check` → 125 files already formatted
- Tests: `node --check` on the page's inline script plus a node render harness → passes (inside `tests/test_web.py::test_page_script_parses_and_renders_the_fixture`, skipped if `node` isn't on PATH)

## What changed
- **Offline mode.** `uv run python -m core.web --offline` (or `AGENT_HUNTER_OFFLINE=1`) runs the A2A door's offline wiring: `core.a2a.offline_build_steps`, `NoModelGateway`, `forbid_model_calls()` (CLI only), and the store in `STORE_DIR` or `core.a2a.OFFLINE_STORE_DIR`. It also starts the mock providers on `MOCK_PROVIDERS_BASE_URL` with the truth dir. `create_app(..., offline=True)` does the same for tests. The rehearsal toggles don't apply offline: config `defaults` = `{mock: true, scorer: "real"}`, and the view reports those values.
- **View fields (contract §1).** `GET /api/config` and each run view have `mode: "live" | "offline"` and `tie_break: "accuracy_first" | "cost"` from `core.eval.stats.accuracy_first()`. Config reads it on every call. A run captures it when it starts.
- **Candidates.** Each row adds `source` (`"slugs"` or `"a2a"`, meaning listed by PROVIDER_A2A_URLS), `transport` and `url`. `reason` now comes from `core.runner.candidate_finder.exclusions()`, the same reasons as the A2A hire result's `excluded`. A new `unlisted: [{id, reason, source}]` lists each card that couldn't be listed (`Registry.unlisted`). `unavailable` (slugs) is unchanged.
- **Page.** The tie wording comes from `tie_break` in two places: the rank step summary and the ranking caption. The page shows an "Offline · stub agents" badge, disables the toggles offline, and labels the rationale as the stub's template text offline. Candidates get an A2A badge and a "Left out" list with full reasons. Unlisted cards get a banner. Also: the offline trial-step summary, the scores legend (adds "error") and the idle hint's run time. The `?demo` fixture uses the current view shape and includes one placeholder A2A agent that is left out with a reason. Its placeholder rationale now describes the accuracy-first rule.
- **AUDIT.md** has one row per panel. **README** demo section gets one added paragraph tail.

## Files touched
- core/web.py: offline option and CLI flag, `tie_break()`, `mode`/`tie_break` in config and view, candidate `source`/`transport`/`url`/`unlisted`, `exclusions()` reasons
- apps/web/index.html: tie wording, offline badge and toggles, candidates panel, fixture refresh, small stale-text fixes (see AUDIT.md)
- tests/test_web.py: tie_break (unset/on/off) in config and view, live candidate source, offline config/runs/store, misleading A2A agent kept and then failing, plain A2A agent excluded with reason, node page check
- apps/web/AUDIT.md: new, panel-by-panel audit
- README.md: demo web app section only (appended sentences)

## Assumptions
- `create_app(offline: bool = False)`. The CLI resolves `--offline or offline_requested()`, the same pattern as `core.a2a`. `create_app` doesn't read `AGENT_HUNTER_OFFLINE` itself, so existing callers and tests can't be flipped by the shell environment.
- The run view's `tie_break` is captured when the run starts, because that's the rule its Ranker uses. `/api/config` reads it live.
- The `source` values are `"slugs"` and `"a2a"`. The contract says only "listing source", without naming values.
- The `?demo` fixture view uses `mode: "live"`, which is the run shape it mimics. The offline badge never shows during fixture playback, because the header already says "Fixture playback · placeholder data". AGE-68's replay can show the badge from a recorded view's `mode`: `isOffline(V)` checks only that `S.mode !== 'fixture'`.
- Mock-provider startup in offline mode is about 10 lines in `core/web.py` that import `core.a2a._mock_providers_url` / `_offline_paths`. That code lives inside `core.a2a.create_app`'s closure and isn't exported, and I didn't edit `core/a2a.py`, which isn't on my file list. `core.a2a` is imported lazily inside functions because it imports `core.web` at module level (otherwise the import is circular).

## Contract gaps
- None blocking. The value names for the candidates' `source` field and the new `unlisted` list aren't in contract §1. AGE-68 replays them as part of the view, as is.

## Left undone
- **Stub Ranker rationale (pipeline, out of scope).** `core/pipeline/stubs.py` `rank_stub` always writes "wins the tie on cost per document, then latency", even though `decide()` picks by `accuracy_first()`. So offline, the rank summary says "tie broken on trial accuracy" while the stub rationale text says cost. The page labels that text "template text from the offline stub Ranker". The fix is a one-line wording change in `rank_stub`, which needs its own issue. It's marked "left" in AUDIT.md.
- The needs-info and paused states were checked by code path only. No offline example triggers them.

## Merge notes
- core/web.py: AGE-68 adds timeline capture, replay routes and `--export-replay`. Add `LiveRun` hooks around `view()`. Leave `_candidates`, `create_app`'s offline block and `main`'s offline resolution as they are. `main` now builds `create_app(offline=offline)`, so pass any new options alongside it.
- apps/web/index.html: new helpers `tieText`, `tieBreakOf`, `isOffline`, `extraUnlisted` sit right after `money`. `S` gains `serverMode` and `tieBreak`. AGE-68's replay should set `S.mode` to something other than `'fixture'` so `isOffline(V)` follows the recorded view's `mode`.
- tests/test_web.py: the autouse `env` fixture now also clears `PROVIDER_A2A_URLS`, `HW7_CHANGE`, `AGENT_HUNTER_OFFLINE` and `PROVIDER_TRANSPORT`. The `offline` fixture (free ports, tmp STORE_DIR/DOCS_DIR/TRUTH_DIR, 300 ms mock deadline) is reusable for AGE-68's tests. `PAGE_PRELUDE` / `PAGE_CHECKS` is the node harness. Add checks to it rather than writing a second one.
- README.md: I only appended to the end of the **Demo web app** paragraph.

## How to verify
- `uv run pytest tests/test_web.py -q`, then `uv run pytest && uv run ruff check && uv run ruff format --check`
- API: `STORE_DIR=$(mktemp -d) DOCS_DIR=$(mktemp -d) TRUTH_DIR=$(mktemp -d) uv run python -m core.web --offline --port 8300`, then `curl -s localhost:8300/api/config` shows `"mode":"offline"` and `"tie_break":"accuracy_first"`. Each example finishes in about 7 s.
- Browser checks at integration (not possible here):
  1. **Offline badge:** open http://localhost:8300 with the server above. The header shows "Offline · stub agents" and no Mock providers / Real scorer chips. The Rehearsal toggles are disabled with the "Offline: always mock providers and the real scorer" note. The idle hint says about 10 s.
  2. **Tie wording, accuracy_first:** run "Invoice extraction (clean PDFs)". The offline mocks tie. Step 8 reads "Ranked: tie" with "N providers overlap at the top · tie broken on trial accuracy". The ranking caption reads "Confidence intervals overlap · tie broken on trial accuracy." The rationale label reads "Why · template text from the offline stub Ranker".
  3. **Tie wording, cost:** restart with `HW7_CHANGE=off` and run the same example. Both places now say "tie broken on cost".
  4. **Candidates panel with the misleading A2A agent:** start a plain A2A agent on 8941 (e.g. `nandatown a2a serve`, or any agent with a non-invoice card) and `uv run python -m mocks.misleading_card --upstream http://127.0.0.1:8941 --port 8942`. Restart the web app with `PROVIDER_A2A_URLS=http://127.0.0.1:8942,http://127.0.0.1:8949` and run inv-clean. Step 5 lists the agent with an "A2A" badge, qualifying "yes", and the summary includes "1 listed by A2A card". A warning banner shows `http://127.0.0.1:8949/.well-known/agent-card.json left out: card fetch failed (ConnectError)`. Its lane fills with warning cells (`error`), and its scores row is all ✗ at 0%. With `PROVIDER_A2A_URLS=http://127.0.0.1:8941` (the plain card) it shows "no · job type" and appears in the "Left out" list with "doesn't declare job type 'invoice_extraction' (declares [...])". It has no lane.
  5. **`?demo` plays to the end:** open http://localhost:8300/?demo (or the file directly) and click Run trial on clean PDFs. All 8 steps check off. Step 5 shows 8 providers, including `outside-agent` with an A2A badge, "no · job type", the "Left out" line, and "7 of 8 qualify". Step 8 and the caption say "tie broken on trial accuracy". The recommendation is `provider-f`, with the rationale ending "wins it on trial accuracy, then cost per document, at $0.0004 per doc." No offline badge appears, and the header says "Fixture playback · placeholder data". Also try `?demo&moment=rejected` and `?moment=running`.
