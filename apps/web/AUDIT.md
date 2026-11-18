# Demo web app audit (AGE-67, Nov 18)

The page (`apps/web/index.html`) and its API (`core/web.py`) checked against the pipeline as it is
today: A2A agents in the registry (AGE-59/60), accuracy-first tie-breaking as the default (AGE-66),
and offline mode (AGE-45).

How it was checked: `uv run python -m core.web --offline` with the doc server, mock providers,
`DOCS_DIR`, `TRUTH_DIR` and `STORE_DIR` on temp dirs and localhost ports. Each example
(`inv-clean`, `inv-long`, `inv-mixed`, `cover`) was run to the end through `POST /api/runs` and
`GET /api/runs/{job_id}`. `inv-clean` and `inv-mixed` were run again with `PROVIDER_A2A_URLS`
listing the `mocks/misleading_card.py` agent (in front of a plain quote agent) and a URL where
nothing listens. The page's script was rendered in node against the `?demo` fixture and
hand-built offline views (`tests/test_web.py::test_page_script_parses_and_renders_the_fixture`).
No browser was available, so the visual checks are listed in the AGE-67 handoff for integration.

Status: **ok** = matches the API and pipeline, unchanged · **fixed** = changed in AGE-67 ·
**removed** = taken out · **left** = wrong, but fixing it would change pipeline behaviour (out of
scope).

| Panel | Status | Note |
|---|---|---|
| Header chips | fixed | Offline runs show an "Offline · stub agents" badge (from `mode`) instead of the Mock providers / Real scorer chips, which don't apply offline. |
| Rehearsal toggles | fixed | Disabled offline, with a one-line note; `/api/config` `defaults` are fixed offline (`mock: true`, `scorer: "real"`), and the run view reports what was actually used. |
| Example jobs (rail) | ok | `/api/config` lists all four examples; budgets and doc counts match the postings. |
| Hint (idle) | fixed | Gives the run time for the mode: about 10 s offline (the mock deadline), 45–90 s live. |
| Job posting | ok | Description, wants, samples, budget, trial budget and region all come from `posting_view`. |
| 1 · Review | ok | Approve shows the reviewer's note, type, per-doc budget and fields; the stub reviewer fills the same `JobSpec` fields. |
| 1 · Review, rejected (`cover`) | ok | Failed step, rejection box with the reason, steps 2–8 skipped, provider cost 0. |
| 1 · Review, needs info | ok | Questions list renders from `review.questions`; no example triggers it, checked by code path only. |
| 2 · Rubric | ok | Weights (sorted), required fields, consistency checks and the four tolerances match `Rubric`. |
| 3 · Trial documents | fixed | Running summary no longer promises synthetic invoices offline: the offline stub Trial Builder uses the samples with answers only. Done summary was already data-driven. |
| 4 · Eval | ok | Answer-key count from the Eval Builder tap. |
| 5 · Candidates | fixed | Rows now carry `source` (`slugs` or `a2a`), `transport` and `url`. A2A-listed agents get an "A2A" badge, a "listed by A2A card" count in the summary, and the Candidate Finder's full reason for each provider left out (the same `exclusions()` the A2A hire result uses). Cards that couldn't be listed at all (`Registry.unlisted`, e.g. an A2A URL with nothing behind it) get a banner with the reason. "no list price" now has its own short reason. |
| 5 · Candidates, misleading A2A card | ok | Its card claims invoice extraction with a list price, so it is kept and shown as qualifying; the lanes, scores and ranking then show every answer failing (`error`) and 0% accuracy. That is the intended demonstration, not a page bug. |
| 6 · Provider lanes | ok | One lane per kept provider, A2A agents included; timeouts and errors use the warning cell; the unavailable lane still comes from `unavailable`. |
| 7 · Scores | fixed | Legend now says "timeout, error or invalid answer" (A2A agents that can't do the job answer `error`). |
| 8 · Rank step summary | fixed | Was hard-coded "tie broken on cost". Now "tie broken on trial accuracy" or "tie broken on cost" from the run view's `tie_break`. |
| Ranking with CI bars | fixed | CI bars, dashed lower-bound guide and cost/timeouts were ok. The tie caption was hard-coded to cost; now uses the same `tie_break` wording. |
| Recommendation | ok | Recommended provider, version and pitch, accuracy, cost/doc, timeouts and full-job cost all match the `Verdict` and lanes. Insufficient-candidates card renders when fewer than two providers answer. |
| Rationale | fixed / left | Label now says "template text from the offline stub Ranker" offline (it isn't written by the Ranker agent). **Left:** the stub Ranker's template (`core/pipeline/stubs.py` `rank_stub`) still says a tie is won "on cost per document, then latency" even though the pick follows `accuracy_first()`. Changing that text is a pipeline change, out of scope here. |
| Costs footer | ok | Platform spend (0 offline), providers' reported `cost_usd` and total match the store. |
| Error state | ok | A failing step is marked failed, later steps skipped, and the error text shown in a "Stopped" box. |
| Paused state | ok | `paused` marks the Trial Runner failed with the "Paused" box and footer pill; checked by code path (offline mocks don't run out of credit). |
| `?demo` fixture | fixed | Matches the current run-view shape: `mode`, `tie_break` (`accuracy_first`), `mock`, `scorer`, candidates with `source`/`transport`/`url`/`unlisted`, and one placeholder A2A agent left out with its reason. The placeholder rationale describes the accuracy-first tie-break. Still anonymised placeholder data, labelled "Fixture playback". |
| Hire button | ok | Still disabled ("Hiring comes after the MVP"). |
