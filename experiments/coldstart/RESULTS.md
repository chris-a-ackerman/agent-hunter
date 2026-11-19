# Cold-start test: results

Plan: `experiments/coldstart/PLAN.md` (written before the run). This file reports what happened.

## Run 1 (2026-11-18)

**Outcome: hire-only.** The calling agent hired through Agent-Hunter on its first `message/send`
and got a `ranked` result recommending `mock-b@1`. It could not get any invoice extracted, and it
said exactly why: the hire result names the provider but gives no way to reach it.

### Setup as run, against the plan

| | plan | what happened |
| -- | -- | -- |
| Commit under test | record the SHA | `90a05bd` (agent card `version: "0.1.0+90a05bd"`, `mode: "offline"`) |
| Server store | `STORE_DIR=.data/offline-store`, fresh | **Not set.** `.env` sets `STORE_DIR=.data/store`, so the run's records are mixed in with older jobs in `.data/store/`. `.data/offline-store/` does not exist. The run's records are still clear by ID and time: dedupe record `a2a_requests/25145d5b….json`, task `a2a_tasks/task-6dd98815b01c.json`, job `jobs/job-416b7e6d011a.json`, all written 21:18:10–21:18:16 |
| Server stdout | redirect to `run-<n>/server.log` | **Not captured.** No server log exists for this run, so the mock-provider side can't be checked from the server's side |
| Calling agent | fresh session, empty folder holding only the doc, no repo, no memory | **Not cold.** It ran as Claude Code (Opus 5.5) in the repo root, with the repo's `CLAUDE.md`, the project memory index and git status loaded into context. It read the doc from `docs/AGENT_INTERFACE.md` |
| Log location | `run-<n>/log.md` | written to the repo root. Now moved to `run1/log.md` |
| Interventions | none after the task | A SessionStart hook added unrelated instructions (a coaching plugin). The agent made one failed tool search for it and moved on. **After** its final report, the human sent "Can you fix it and re-run it?". The agent asked a question, and the human rejected it. This came after the outcome and didn't change it, but the session was not one-message-only |

Files: `run1/log.md` (the agent's log), `run1/transcript.txt` (session export, also in
`~/coldstart/`), `run1/AGENT_INTERFACE.md` (the doc as the agent saw it; identical to
`docs/AGENT_INTERFACE.md` at `90a05bd`).

Did the non-cold setup help the agent? From the transcript: apart from the doc, it read no repo
file during the run: no `examples/`, no code, no `mocks/`. It took the sample and its answer from
the doc's own §7 example. The extra context was available but not used, and the agent still
failed at step 2. That makes the step-2 blocker stronger evidence, not weaker.

### Requests and responses

All from `run1/log.md`, in order. Each logged hire response was checked against the server's
stored task: `result` in the log equals `.data/store/a2a_tasks/task-6dd98815b01c.json` exactly.

| # | time | request | response | purpose |
| -- | -- | -- | -- | -- |
| 1 | 21:18:00 | `GET :8940/.well-known/agent-card.json` | 200, card, `mode: offline` | is it up |
| 2 | 21:18:10 | `POST :8940/` `message/send`, `request_id: ap-weekly-invoices-20261118-01` | 200, task `task-6dd98815b01c` `completed`, `job_status: ranked`, recommended `mock-b@1` | hire |
| 3 | 21:18:23 | `POST :8940/` `tasks/get` `task-6dd98815b01c` | 200, the same task | look for an endpoint |
| 4–6 | 21:18:23 | `GET :8101/.well-known/agent-card.json`, `/mock-b/.well-known/agent-card.json`, `/` | 404 ×3 | try the mocks' address from doc §5.7 |
| 7 | 21:18:31 | `GET :8101/openapi.json` | 200, routes incl. `POST /p/{slug}/tasks` (ProviderTask) | find a route |
| 8 | 21:18:40 | `GET :8101/p/mock-b/.well-known/agent-card.json` | 200, `price_per_doc_usd: 0.003` | the hired provider's card |

The hire body: one sample (`samples/invoice_clean/sample_2.pdf` with its answer, copied from
doc §7), `output_schema` with the eight Invoice keys, `n_real_docs: 50`, `job_budget_usd: 1.0`,
`trial_budget_usd: 0.1`, `deadline_open_ack: true`.

The verdict: `recommended` `mock-b@1`, accuracy 0.80, CI [0.80, 0.80] (one-document trial),
`cost_per_doc` 0.001. Runner-up `mock-a@1` 0.70, `mock-c@1` 0.20, `mock-d@1` 0 (1 timeout),
`mock-e@1` 0. Spend $0.0065 (providers), $0 platform.

Metrics from the plan: **requests to the first ranked result: 2** (card, then one
`message/send`). **Errors:** none (no `failed`, `rejected` or JSON-RPC error). **Time to first
hire result:** 16 s from the first request (job ran 02:18:10.14Z → ranked 02:18:16.76Z, about
6.6 s of pipeline). **Agent's report vs server:** it matches (recommended, scores, IDs, spend).

### What the calling agent did with the result

1. It parsed the hire result and saw `recommended: "mock-b@1"`. It found no URL there, which is
   correct: doc §1 says "It contains no endpoint URL."
2. It re-read the task with `tasks/get` to check that nothing else had been returned.
3. It tried the mock providers' address that the doc gives only as where they run
   (`localhost:8101`). Three standard paths returned 404. Then `/openapi.json` gave it the route
   list, including `POST /p/{slug}/tasks` and the card route.
4. It fetched `mock-b`'s card and read the ProviderTask schema. That schema needs a
   `document_url` of the form `{DOCS_BASE_URL}/d/{doc_id}`, and the agent had no `doc_id`. It
   stopped there. It sent **no** task to any provider (only GETs, rows 4–8).
5. It reported the outcome as hire-only and named three blockers: no endpoint in the hire
   result; no way to give the provider a document; and the offline mocks can't really extract
   anything (from doc §5.7).

One misreading: the agent flagged "card lists $0.003/doc but the result reports $0.001/doc" as a
mismatch. That's by design (PLAN.md D19: the card price is a list price used for budget
filtering, while `cost_per_doc` is what the provider reported spending). The doc doesn't
contrast the two, so it counts as a calling-agent error with a doc-clarity note, not a bug.

### Outcome against the success criteria

**Cold-start plan:** criterion 1 (hire) holds: a `completed` task, `ranked`,
`verdict.recommended` set, for a request the agent built itself. Criterion 2 (use) fails:
no extraction was attempted, so there is no vendor or total to score. The blocker was stated
clearly. Level: **hire-only**.

**PLAN.md §1.6:** this run doesn't measure any of the five criteria. They need real jobs (40),
real providers and real-job accuracy, and offline scores are synthetic. What it does show,
indirectly: the step "requester hires the recommended provider and runs the real job" has no
path through the interface today. All the §1.6 criteria that compare "recommended" against
real-job accuracy (selection quality, comparison, representativeness) depend on that step.

### Blockers, classified

| # | blocker | class | evidence |
| -- | -- | -- | -- |
| B1 | The hire result names the recommended provider (`slug@version`) but gives no endpoint, and Agent-Hunter has no method to forward a job | **interface limitation** | log row 2 (no URL in `hire_result`); `core/a2a.py` `hire_result()` at `90a05bd` builds no endpoint field; doc §1 at `90a05bd` says so; the agent stopped at this point (transcript) |
| B2 | A caller can't put its own invoice where a provider can fetch it: providers take a `document_url` on Agent-Hunter's doc server, which serves only `DOCS_DIR` on its host, and there's no upload | **interface limitation** | log row 7 (ProviderTask needs `document_url`); `core/docserver.py` serves only `GET /d/{doc_id}.pdf` from `DOCS_DIR`; doc §1 "does not accept uploaded documents"; trial doc IDs are never returned |
| B3 | The offline mocks don't read PDFs. They answer only for doc IDs they hold an answer key for | **project limitation** (offline mode by design) | doc §5.7; `mocks/fake_providers/app.py` `fulfil()` returns `404 unknown_document` with no key. This would have blocked a real extraction even with B1 and B2 fixed |
| B4 | The task gives the agent no invoice | **test-setup** (deliberate, PLAN.md "No invoice is supplied") | task text. It only blocks because of B2: with an upload path the agent could have used a sample |

Setup problems (none caused the failure, but they weaken the record): `STORE_DIR` not set (mixed
store); no server log; the calling agent was not cold (repo context and memory loaded, cwd = repo
root); log written outside `run1/`; hook injection; a human message after the outcome. Tie-break
rule: none of these could have caused B1–B3, which come from the code and the doc, so no re-run
was needed to classify them.

Doc bugs found while checking: two wrong cross-references (§1 pointed to "§4.3" for sample
paths, which are in §3.3; §2 pointed to "§5.6" for offline/live, which is §5.7). Both fixed below.

### The fix: tell the caller how to reach the hired provider (B1)

Why B1: it was the first wall, and the agent never got past it. It is also the only blocker
with a small fix. B2 needs an upload or hosting feature. B3 is what offline mode is, and only
live providers remove it. With B1 fixed, the hand-off from hire to use can at least be shown
working end to end offline.

Change (`core/a2a.py`):
- A `ranked` hire result with a `verdict.recommended` now has a `hire` key:
  `{"provider", "transport", "url", "card_url"?}`. This is the same endpoint the Trial Runner
  used for that provider during the trial. `http` → `url` is `{base_url}/p/{slug}/tasks` plus the
  card URL. `a2a` → `url` is the provider's A2A URL. `CandidateTap` records it from the
  Candidate Finder's kept list (new `provider_contact()`).
- The key is absent when there is no pick (`insufficient_candidates`, rejected, needs_info,
  paused) and for a run recovered after a restart. Only the recommended provider's endpoint is
  given. `contracts/` is unchanged: the hire result is built in `core/a2a.py`.

Tests (`tests/test_a2a_offline.py`):
- `test_hire_result_says_how_to_reach_the_recommended_provider`: a real offline hire, then
  **only** the `hire` block is used. It GETs `card_url`, POSTs a ProviderTask to `url`, and gets a
  200 ProviderResult back.
- `test_no_hire_key_without_a_recommended_provider` (rejected job).
- `test_a2a_provider_contact_is_its_a2a_url`.
- The first and third fail on `90a05bd` and pass with the fix. The full suite passes:
  616 passed, 8 skipped, and `ruff check` is clean. The 4 `PytestUnhandledThreadExceptionWarning`
  (`SystemExit: 3`) also occur on `90a05bd` without the change (613 passed, same 4 warnings).

Doc (`docs/AGENT_INTERFACE.md`):
- §1 and §4.2 now describe `hire`.
- A new §4.3, "Calling the hired provider", covers the ProviderTask fields, replies and error
  codes, the a2a variant, and how `document_url` works. It states the B2 gap plainly. It also
  states the offline behaviour: the mocks answer only for known doc IDs, and the one fixed ID is
  the demo invoice `1070b4b25d3c`.
- §7 was re-captured with the fixed code. It now includes the hand-off call and its response
  (vendor `Anderson LLC`, total `12073.68`, labelled as the mock replaying its key).
- The cross-reference bugs are fixed.

**Confound for run 2:** two things change at once. The code adds `hire`, and the doc now
describes the existing offline demo document. Without the second, an offline caller has no
document ID the mocks will answer for, so run 2 could not reach "full" offline at all. A "full"
in run 2 shows the hand-off works, not real extraction: the vendor and total will come from the
mock's answer key for `1070b4b25d3c`.
