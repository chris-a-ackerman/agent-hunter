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

## Rerun: run 2 (2026-11-18)

**Outcome: full, by the plan's criteria, but only as a hand-off test.** A headless agent with
only the doc hired through Agent-Hunter (recommended `mock-a@1`), sent one invoice task to the
URL from the hire result's `hire` field, and reported vendor **Anderson LLC** and total
**12,073.68**. These match the answer key exactly (all eight fields). The invoice was the offline
demo document `1070b4b25d3c`, and the provider answered from its stored key without reading a
PDF. The agent said so itself.

**Validity problem.** Run 2 is not a valid cold-start test of step 2. The doc the agent was given
(`docs/AGENT_INTERFACE.md` at `7fa88f5`) contained both pieces of the step:
- **The expected output:** §7 showed the provider's response for `1070b4b25d3c` verbatim, with
  vendor `Anderson LLC` and total `12073.68`.
- **A near-scripted call:** §4.3 gave the exact `document_url` to send to `hire.url`.

So the field match below is **not evidence of correctness**: the agent could have reported the
values straight from the doc. What run 2 does show is narrower. The agent took `hire.url` and
`hire.card_url` from the hire result it received and used them to reach the hired provider
(rows 3–4 below, with no guessed paths). The doc was fixed for run 3.

### Setup as run

The plan's "Run 2" section was written before the run.

| | |
| -- | -- |
| Commit under test | `7fa88f5` (agent card `version: "0.1.0+7fa88f5"`, `mode: "offline"`) |
| Mode | offline. The fix doesn't let callers send their own invoices (B2), so no `invoices/` folder and no extra task line |
| Server | `STORE_DIR=.data/offline-store uv run python -m core.a2a --offline --port 8940`. The store was fresh (it didn't exist before). Run 1's server (old code) was stopped first. stdout/stderr → `run2/server.log` |
| Calling agent | `claude -p "<task verbatim>" --output-format stream-json --verbose --max-turns 40 --allowedTools "Bash(curl:*)" "Bash(base64:*)" "Read" "Write"`, run from `~/coldstart2/` (outside the repo) holding only `AGENT_INTERFACE.md`, identical to `docs/AGENT_INTERFACE.md` at `7fa88f5`. Claude Code 2.1.328, `claude-opus-5-5`, permission mode `default` |
| Run | 19 turns, 97 s, $0.78 of agent model spend, 4 permission denials, no human input |

Files in `run2/`:
- `log.md`: the agent's log.
- `transcript.jsonl`: the full stream-json session.
- `agent-tools/`: the request bodies and scripts it wrote.
- `server.log`: the door's stdout/stderr.
- `server-store/`: the dedupe record, task and job record for this run.

Setup problems that remain (none affected the outcome; evidence in `transcript.jsonl`):
- **User-level Claude Code config still loads in a headless session.** The init message shows
  the user's plugins and MCP servers (Linear, Notion, Supabase, Google Drive and others connected).
  A coaching plugin's SessionStart hook added instructions, and the agent's first action was one
  failed `ToolSearch` for it. It never called any of those MCP tools. No user-level or project
  `CLAUDE.md` applied, and the project memory for `~/coldstart2` was new.
- **The tool allowlist blocked the agent's own approach twice.** It wrote a Python logging
  script, but `python3` wasn't allowed (3 denials), so it switched to curl and wrote `log.md` by
  hand. An `Edit` to raise its offer from $0.004 to the card's list price of $0.01 was also
  denied, so the task went out offering $0.004. These are test-setup effects. The second one
  shows something about the interface, noted below.
- **`server.log` shows only the door's outbound calls** (card fetches and the trial's
  `POST /p/{slug}/tasks`), not the agent's direct calls to the mock server, which doesn't log
  requests. The provider call is evidenced by the transcript (the tool call and the raw curl
  output), not by a server log.

### Requests and responses

Four requests in total, all in `run2/log.md`. Checked: the logged hire response's `result` equals
`server-store/a2a_tasks/task-2918cfe58091.json` exactly. The logged provider card and provider
response are byte-identical to the curl output in the transcript. The log was written by hand
after the calls, but it is verbatim.

| # | request | response |
| -- | -- | -- |
| 1 | `GET :8940/.well-known/agent-card.json` | 200, `version 0.1.0+7fa88f5`, `mode: offline` |
| 2 | `POST :8940/` `message/send`, `request_id: ap-weekly-invoices-2026-11-18-01`, one sample (`invoice_clean/sample_2.pdf` with its answer, from doc §7), the eight-key schema, `n_real_docs: 50`, `job_budget_usd: 1.0` | 200, `task-2918cfe58091` `completed`, `ranked`, recommended `mock-a@1` (accuracy 1.0, CI [1.0, 1.0], $0.004/doc). Others: `mock-c@1` 0.90, `mock-b@1` 0.50, `mock-d@1` 0 (1 timeout), `mock-e@1` 0. `hire: {provider: mock-a@1, transport: http, url: http://localhost:8101/p/mock-a/tasks, card_url: …/p/mock-a/.well-known/agent-card.json}`. Spend $0.0065 |
| 3 | `GET` `hire.card_url` | 200, `price_per_doc_usd: 0.01` |
| 4 | `POST` `hire.url` with a ProviderTask: `task_id: ap-inv-0001`, `document_url: http://localhost:8200/d/1070b4b25d3c.pdf`, the posting's schema, `deadline_ms: 30000`, `offered_price: 0.004` | 200 ProviderResult, `output` = the full invoice (Anderson LLC, BILL-9567, 2026-05-23, USD, 2 line items, subtotal 11363.46, tax 710.22, total 12073.68), `cost_usd: 0.004` |

Metrics: **requests to the first ranked result: 2.** **Errors:** none from either server.
**Time to first hire result:** the pipeline ran in 6.4 s (02:31:39.29Z → 02:31:45.68Z), and the
whole session took 97 s. **Agent's report vs server:** it matches (recommended, scores, spend,
extracted fields).

### Did the calling agent use the result?

Yes, and only through the hire result. Both provider calls (rows 3 and 4) went to exactly
`hire.card_url` and `hire.url` from row 2's response. Unlike run 1, there were no guessed paths,
no `/openapi.json` and no 404s. It picked the document ID from doc §4.3. Its report says plainly
that the extraction is a stored answer, not a reading of the PDF, and names the remaining
blockers (no way to send its own invoices; offline only).

### Outcome against the success criteria

- **Criterion 1 (hire): holds.** A `completed` task, `ranked`, `verdict.recommended` set, for a
  request the agent built.
- **Criterion 2 (use): holds as the plan words it.** The extraction came from the recommended
  provider via its endpoint, not from the agent reading a PDF or copying its own sample's
  answer. The vendor matches exactly and the total to the cent, against
  `mocks/fake_providers/fixtures/1070b4b25d3c.json` (the plan's listed key for this document).
- **Level: full.** Read it as "the hire → use hand-off works for a cold caller", **not** "an
  invoice was extracted". The mock replays its answer key, so this is not extraction ability.
- **PLAN.md §1.6:** still not measured (no real jobs, no real providers). What changed is that the
  path those criteria assume (requester hires the pick and runs the real job) now exists in the
  interface for offline mocks.

### What improved vs run 1

- **B1 is resolved.** The agent reached the hired provider in 2 requests straight from the hire
  result: no URL guessing, no `/openapi.json` discovery and no 404s (run 1: 5 probes, then it
  stopped).
- **It got an extraction back**, while run 1 got none. Outcome hire-only → full (with the caveat
  above).
- **The setup is cleaner:** a separate store, a server log, the agent outside the repo, headless
  with no human messages, and the commit identified on the card.

### What didn't improve

- **B2, no way to send your own invoice: unchanged (interface limitation).** Evidence:
  §4.3 of the doc says so; `core/docserver.py` serves only `DOCS_DIR`; the agent's report names
  it as the first blocker to real weekly use. The run reached "full" only because the doc names
  a document ID the mocks already know.
- **B3, offline mocks can't read PDFs: unchanged (project limitation, offline by design).**
  Evidence: doc §5.7; `mocks/fake_providers/app.py` `fulfil()`; the output equals the stored key
  in all fields.
- **The pick is unstable.** Same posting shape, three runs, two different picks (`mock-b@1` in run
  1; `mock-a@1` in run 2 and in the doc's §7 capture), each from a one-document trial with a
  zero-width CI. The doc warns about this, and the agent repeated the warning. It's a project
  limitation of 1–3-sample offline trials, not something the fix touched.
- **New, minor (interface):** the provider accepted `offered_price: 0.004`, below its card's
  $0.01 list price, and charged $0.004. Nothing enforces or explains the offered vs list price
  for real jobs. Doc §4.3 only says the card price is the list price. This was exposed by a
  test-setup denial, but the behaviour belongs to the interface (the mocks ignore the offer).

### Blockers in run 2, classified

| blocker | class | evidence |
| -- | -- | -- |
| Can't send the agent's own invoice (B2) | interface limitation | doc §4.3; `core/docserver.py`; agent's final report |
| Offline mocks answer only from stored keys (B3) | project limitation | doc §5.7; output = fixture key exactly |
| `python3` and `Edit` denied, so the agent logged by hand and couldn't change its offer | test-setup | 4 `permission_denied` events in `transcript.jsonl`; no effect on the outcome |
| User plugins, MCP servers and a SessionStart hook loaded in the headless session | test-setup | `hook_response` and `init` events in `transcript.jsonl`; one failed `ToolSearch`, no other use |

### What remains unproven

- **Real extraction.** Offline only. No live providers, no PDF actually read, and no Parley spend
  on the provider side. Live mode is still unchecked against this doc.
- **A caller's own invoices.** One invoice, and it was a fixed demo ID the doc gave out, not an
  invoice the caller brought. B2 is still open.
- **Generality of the caller.** One calling agent (Claude Code, `claude-opus-5-5`) and one run per
  setup. The agent was a model like ours, with tools and an instruction style like ours.
- **Generality of the providers.** Our own mock providers on the `http` transport. The `a2a`
  branch of `hire` is unit-tested only, and no outside provider was hired.
- **No outside builder.** The doc was written by the same people (and the same model family) as
  the code. Nobody outside the project has tried to build against it.
- **The doc confound.** Run 2 changed the code (`hire`) and the doc (§4.3 names the demo
  document) together. Run 1 shows `hire` alone was necessary. This run doesn't show it was
  sufficient without the doc's demo ID, and the plan's analysis says it wasn't.

## Run 3 (2026-11-18)

**Outcome: full.** The agent's reported values match the hidden answer key. A headless agent
with only the corrected doc hired `mock-a@1` through Agent-Hunter. It sent one of the listed
offline invoices, `000145785a2b`, to `hire.url` and reported vendor **Niemeier** and total
**€6,559.71**. The server's answer key `data/truth/000145785a2b.json`, which the agent never saw,
says vendor `Niemeier`, total `6559.71`, currency `EUR`. Vendor: exact match. Total: exact match.
The provider's output equals the key in all eight fields. The mock replayed the key; nobody read
the PDF.

### Setup as run

The plan's "Run 3" section was written before the run, and the doc fix was committed before it.

| | |
| -- | -- |
| Commit under test | `0c4ade5` (agent card `version: "0.1.0+0c4ade5"`, `mode: "offline"`) |
| Doc | `docs/AGENT_INTERFACE.md` at `0c4ade5`: shape-only §7 example, and an "Offline test data" note in §4.3 listing `000145785a2b`, `002b4c406d2e`, `00ab87544902`, `00ccee84b0e9` with no answers and no order. `~/coldstart3/AGENT_INTERFACE.md` is byte-identical. Checked: neither `Niemeier` nor `6559` appears in it |
| Server | the run-2 store was moved to `.data/offline-store-run2`; `STORE_DIR=.data/offline-store` (fresh); stdout/stderr → `run3/server.log`. Docs from `data/docs`, keys from `data/truth` (the `.env` values) |
| Calling agent | the same `claude -p` command, allowlist and model (`claude-opus-5-5`) as run 2, in `~/coldstart3/`; task text verbatim, no extra line, no human input |
| Run | 15 turns, 81 s, $0.59 of agent model spend, 2 permission denials (its `python3` logging script again), then curl plus `Write` |

The same test-setup carry-overs as run 2: the user-level plugins, MCP servers and SessionStart
hook loaded, and the agent's first action was again one failed `ToolSearch` for the hook's tool.
It never touched `data/truth` or anything outside its folder and the three servers. The doc
server and mock server don't log requests, so `server.log` shows only the door's trial traffic.
The agent's provider calls are evidenced by the transcript.

Files in `run3/`:
- `log.md`: the agent's log.
- `transcript.jsonl`: the full stream-json session.
- `agent-tools/`: the hire envelope, the ProviderTask and the unused logging script.
- `server.log`: the door's stdout/stderr.
- `server-store/`: the dedupe record, task and job record.

### Requests and responses

Four requests, all in `run3/log.md`. Checked: the hire response in the log and in the raw curl
output both equal `server-store/a2a_tasks/task-95581835e17c.json` exactly. The provider
response in the log is byte-identical to the curl output.

| # | request | response |
| -- | -- | -- |
| 1 | `GET :8940/.well-known/agent-card.json` | 200, `0.1.0+0c4ade5`, offline |
| 2 | `POST :8940/` `message/send`, `request_id: ap-weekly-invoices-0001`, one sample (`invoice_clean/sample_2.pdf` with its answer from §7), the eight-key schema, `n_real_docs: 50`, `job_budget_usd: 1.0` | 200, `task-95581835e17c` `completed`, `ranked`, recommended `mock-a@1` (1.0, CI [1.0, 1.0], $0.004/doc). Others: `mock-c@1` 0.80, `mock-b@1` 0.55, `mock-d@1` 0 (1 timeout), `mock-e@1` 0. `hire.url` `http://localhost:8101/p/mock-a/tasks`. Spend $0.0065 |
| 3 | `GET` `hire.card_url` | 200, `price_per_doc_usd: 0.01` |
| 4 | `POST` `hire.url`: `document_url: http://localhost:8200/d/000145785a2b.pdf`, the posting's schema, `offered_price: 0.01` | 200 ProviderResult, `output` with vendor `Niemeier`, `IN-88515`, 2026-09-25, EUR, 5 line items, subtotal 5512.36, tax 1047.35, total 6559.71; `cost_usd: 0.004` |

Metrics: **2 requests to the first ranked result.** **No errors.** The pipeline ran in 5.4 s
and the session took 81 s. **Agent's report vs server: matches** (recommended, scores, spend,
extracted values).

### What it did

It read the doc, hired, and took `hire.url` and `hire.card_url` from the hire result. It picked
the first ID in the doc's list, offered the card's list price, and sent one task. It also checked
the output itself: the line items sum to the subtotal, the tax is 19%, and subtotal plus tax
equals the total. It reported the vendor and total, and it said plainly that offline mocks look
up stored answers and that there's no way to send its own invoices.

### What run 3 proves that run 2 didn't

- **The answer check is real now.** The expected values weren't in anything the agent could see,
  and they match a key it never read. So the agent correctly relayed what the hired provider
  returned for an invoice it had no answer for. Run 2's match could have been copied from its doc.
- **The hand-off works without a script.** The doc gave a list of IDs and the URL pattern, but no
  call order and no example call. The agent put the ProviderTask together from §4.3's field table
  and still reached the provider in two requests straight from the hire result.
- **The picks agree, but that's chance.** `mock-a@1` was picked in runs 2 and 3, and in the doc's
  earlier §7 capture. `mock-b@1` was picked in run 1.

### Blockers in run 3, classified

| blocker | class | evidence |
| -- | -- | -- |
| No real-weekly-use path: can't send own invoices (B2) | interface limitation | doc §4.3; `core/docserver.py`; the agent's report |
| Offline mocks replay keys (B3) | project limitation | doc §5.7; output = key in all fields |
| `python3` denied, so it logged by hand | test-setup | 2 `permission_denied` events; no effect on the outcome |
| User plugins, MCP servers and hook loaded | test-setup | `init` and `hook_response` events; one failed `ToolSearch` |

### What remains unproven

- **Real extraction.** The offline mock providers replay stored answer keys. They don't read the
  PDF. A "full" here proves the hire → hand-off → report chain, not extraction quality. Live mode
  is still unchecked against this doc.
- **A caller's own invoices.** Callers can't send their own invoices (B2). The agent could only
  use an invoice the doc server already held, from a list the doc gave it.
- **One agent.** One calling agent per run (Claude Code, `claude-opus-5-5`, a model like the one
  that wrote the doc), and one invoice per run.
- **Our own providers.** Only our own mock providers on the `http` transport. The `a2a` path is
  unit-tested only. No outside provider, and no outside builder of either side.
- **The pick varies between runs.** Every trial has one document and zero-width CIs.
  Identical postings picked `mock-b@1` (run 1) and `mock-a@1` (runs 2 and 3).
- **Reusing the test IDs.** The four listed IDs and one answer (`000145785a2b`) are now recorded
  in this repo. A future run should use a fresh list from `data/truth`, or keep this file out of
  the agent's reach (it already is when the agent works outside the repo).
