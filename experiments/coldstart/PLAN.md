# Cold-start test: can an outside agent use Agent-Hunter from the docs alone?

Written 2026-11-18, **before any run**. Nothing below has been observed yet. Results go in a
separate `RESULTS.md`; this file is not edited after the first run, except to add a dated
"amendments" note at the end if the setup has to change.

## Question

Given only `docs/AGENT_INTERFACE.md` and a running offline server, can a general-purpose agent
that has never seen this repo (1) hire an extraction agent through Agent-Hunter and (2) use what
it gets back to have a real invoice extracted?

## Task given to the calling agent (verbatim)

> You're an accounts-payable assistant. Your company needs supplier invoices turned into
> structured JSON every week. A hiring service for AI agents is running at
> http://127.0.0.1:8940, described in AGENT_INTERFACE.md. Use it to choose an extraction agent
> for this work, then use what it gives you to get at least one invoice extracted, and report
> the vendor and total. Log every request you send and every response you get in log.md. If you
> can't finish, say exactly what blocked you.

Nothing else is added to the prompt: no hints about mock providers, ports, sample files or
the repo.

## Setup

- **Server:** from the repo root, at the commit under test (record its SHA in RESULTS.md):

  ```bash
  STORE_DIR=.data/offline-store uv run python -m core.a2a --offline --port 8940
  ```

  `STORE_DIR` is set explicitly because the repo's `.env` sets `STORE_DIR=.data/store`, which
  would otherwise override the offline default and mix this run with older jobs. Before the
  run, `.data/offline-store/` must not exist (move any old one aside under a new name; don't
  delete it). The doc server (8200) and mock providers (8101) start in the same process; check
  nothing else is listening on 8940, 8200 or 8101 first.
- **Calling agent:** a fresh agent session (Claude Code, default model, no project memory)
  whose working directory is a new empty folder `experiments/coldstart/run<n>/` containing
  only a copy of `docs/AGENT_INTERFACE.md`. It may use the shell, curl and Python, and may reach
  `127.0.0.1` on any port. It is **not** given the repo or the `examples/` folder: if it wants a
  sample or an invoice, it has to get there from the doc. One run = one session, no human
  replies after the task is sent (if it asks a question, the answer is "Do your best with what
  you have.", logged as an intervention).
- **No invoice is supplied.** The task doesn't name one, so the agent has to find or reference
  one itself. This is deliberate and mirrors the task text; we record which invoice it used.
- **Budget:** offline, so no model spend on our side. Cap the calling agent's session at 45
  minutes wall time.

## What counts as a useful result

Both must hold:

1. **Hire:** the calling agent receives a hire result for its own job: a `completed` task
   whose `hire_result` has `job_status: "ranked"` and a non-null `verdict.recommended`, for a
   request the agent itself built and sent.
2. **Use:** the agent then uses that result to get at least one invoice extracted, and reports
   a vendor and a total, where
   - the extraction came from the provider the hire result recommended (or, for a tie, one of
     `tied`), not from the agent reading the PDF itself or copying a sample's answer from its
     own request; and
   - the reported vendor and total match the answer key for that invoice (vendor: exact string
     after trimming; total: within $0.01).

Outcome levels, for RESULTS.md:

| level | meaning |
| -- | -- |
| **full** | 1 and 2 hold |
| **hire-only** | 1 holds; 2 fails or isn't attempted, and the agent states what blocked it |
| **hire-only, unexplained** | 1 holds; 2 fails and the agent claims success or gives no clear blocker |
| **no hire** | 1 fails |

We also record: number of requests to reach the first `ranked` result; every `failed` /
`rejected` / JSON-RPC error and whether the agent recovered; time to first hire result;
whether the agent's report of the verdict matches what the server sent.

## How we check

1. **Server-side record (ground truth for 1).**
   - `.data/offline-store/a2a_tasks/task-*.json`: every task the door returned, including
     `failed` ones (invalid requests are only here).
   - `.data/offline-store/a2a_requests/*.json`: one dedupe record per string `request_id`, with
     `job_id`, `terms_digest`, state and the final task. A request without a string
     `request_id` has no record here; that's expected, not a gap.
   - `.data/offline-store/jobs/job-*.json`: the stored posting, status history and verdict per
     job.
   - The server's stdout/stderr (redirect to `run-<n>/server.log`), which shows every task the
     Trial Runner sent to the mock providers. Any request to the mocks that isn't part of a
     trial shows up here too.
2. **Calling agent's log.** `run-<n>/log.md`, plus the session transcript. We match each
   logged request/response to a server-side task by task ID and `request_id`. Mismatches
   (a response in the log the server never sent, or a request missing from the log) are
   reported as logging errors.
3. **Answer key (ground truth for 2).** Depending on the invoice the agent used:
   - a bundled sample (`samples/<set>/sample_<k>.pdf`): the `answer` in
     `examples/<set>.json`;
   - a trial document (`/d/<doc_id>.pdf` on the doc server): `data/truth/<doc_id>.json`;
   - the mock fixture `1070b4b25d3c`: `mocks/fake_providers/fixtures/1070b4b25d3c.json`;
   - anything else: we say so, and score 2 as not verifiable.
   We compare the agent's reported vendor and total, and also every field it extracted, to the
   key, and note where the value came from (which endpoint, which response in log.md).

We expect from reading the code (a prediction, to be checked, not a result): step 1 is
reachable from the doc; step 2 probably isn't, because the hire result carries only a provider
ID, Agent-Hunter has no way to run the real job, and the offline mocks only answer for
documents they hold an answer key for. If the agent does complete step 2, we look closely at
how (e.g. calling the mocks directly at `localhost:8101`, which the doc mentions only as where
they run), since that tells us what the interface is missing.

## Telling interface limitations from test-setup problems

Each blocker or failure in the run is classified once, by these rules, decided now:

**Project / interface limitation:** the agent did what the doc says and the system still
couldn't give it what the task needs. Examples:
- the hire result has no endpoint or way to call the recommended provider;
- no way to submit an invoice that isn't already on the server (sample paths are server-side);
- the offline mocks can't extract an invoice without a stored answer key;
- a documented behaviour that the server doesn't actually have, or a doc statement that's
  wrong (also logged as a doc bug);
- an error reply too vague to act on (e.g. `pipeline_error` with only an exception class).

**Test-setup problem:** something we control in the harness, not the product, caused it.
Examples:
- server not running, wrong port, ports 8200/8101 taken, stale store from an earlier run;
- the calling agent couldn't read `AGENT_INTERFACE.md` or reach `127.0.0.1`;
- sandbox or permission prompts blocking the agent's shell or network;
- session time cap hit while the agent was still making progress;
- the task prompt was altered or a human hint was given.

**Calling-agent error:** the doc covers it and the agent got it wrong (e.g. sent the posting
as a JSON object instead of a string in the text part, omitted `output_schema` after the doc
says it's needed). Counted separately; repeated errors on the same point are read as a
possible doc clarity problem, noted but not reclassified.

Tie-break: if a failure could be either a limitation or a setup problem, we re-run once with
the setup issue fixed. If it still fails, it's a limitation; if it passes, it was setup. Any
re-run is a new `run-<n>` and both are reported.

## Out of scope

Live mode, real providers, multiple concurrent callers, and any change to Agent-Hunter during
the test. Findings become issues; they are not fixed mid-run.

## Amendments

**2026-11-18, after run 1.** The calling agent's working folder moves **outside the repo**
(e.g. `~/coldstart<n>/`, holding only a copy of `docs/AGENT_INTERFACE.md`). Claude Code
loads `CLAUDE.md` from parent directories, so a folder under `experiments/coldstart/` would give
the agent the repo's instructions, which breaks the no-repo rule. Its `log.md`, the server log and
the transcript are copied into `experiments/coldstart/run<n>/` after the run. Everything else is
unchanged. Run 1 broke this setup in other ways too (see RESULTS.md).

## Run 2

Written 2026-11-18, **before run 2**.

**Mode: offline.** The fix after run 1 (RESULTS.md, "The fix") adds the recommended provider's
endpoint to the hire result (`hire`). It does **not** let a caller send its own invoice:
providers still fetch documents from Agent-Hunter's doc server, which has no upload (blocker B2).
Live providers would need a document the caller can't provide, and offline mocks can't read new
invoices either, so live mode would add cost without making step 2 any more reachable. Offline
tests what changed: can a cold caller go from the hire result to a provider call?
`~/coldstart2/` therefore holds only the doc, with **no** `invoices/` folder, and the task text
gets no extra line.

**Setup:** run 1's server is stopped. The door is started offline on 8940 with
`STORE_DIR=.data/offline-store` (fresh; it didn't exist after run 1, whose records are in
`.data/store/`) and stdout/stderr to a server log. The calling agent is a headless
`claude -p` session in `~/coldstart2/` (outside the repo), with the task text above verbatim,
`--max-turns 40`, and tools limited to `Bash(curl:*)`, `Bash(base64:*)`, `Read` and `Write`. No
human input while it runs.

**Success criteria:** unchanged from the top of this file (criteria 1 and 2, the same outcome
levels and the same answer keys). A "full" offline result shows that the hand-off from hire to
use works. It is not a real extraction: an offline mock answers from its answer key.

## Run 3

Written 2026-11-18, **before run 3**.

**Why:** run 2's doc contained the expected output (Anderson LLC / 12073.68 for
`1070b4b25d3c`) and the exact `document_url` to send, so its field match proves nothing about
correctness (RESULTS.md, "Validity problem"). For run 3 the doc shows response shapes only, with
no extracted values. Its "Offline test data" note lists four doc IDs the offline doc server holds
(`000145785a2b`, `002b4c406d2e`, `00ab87544902`, `00ccee84b0e9`). It gives no answers and doesn't
say which ID to use or what to call in what order. None of the four is a copy of a bundled sample
invoice. The §7 hire body still shows a sample with its answer (`invoice_clean/sample_2.pdf`):
that is a required input for an offline hire, and it is not the answer to any listed ID.

**Setup:** same as run 2. Mode offline (callers still can't send their own invoices). The
run-2 store is moved aside to `.data/offline-store-run2`, so `.data/offline-store` starts fresh.
The door runs at the commit that has the edited doc, with stdout/stderr to `run3/server.log`. The
calling agent works in a new folder `~/coldstart3/` holding only the edited doc, runs headless
with the exact command and allowlist of run 2, gets the task text verbatim with no extra line,
and gets no human input.

**How the answer is checked:** the vendor and total the agent reports are compared with the
answer key in the server's truth store, `data/truth/<doc_id>.json`, for the doc ID it actually
sent (read from its `document_url` in the transcript). The agent never sees that file: it
isn't served, isn't in its folder, and its values aren't in the doc. Vendor: exact string after
trimming. Total: within $0.01. We also record whether the provider's output equals the key in
all fields, and whether the agent's reported values equal the provider's output (they could
differ if the agent misreads or alters them). Success criteria and outcome levels are
unchanged. A "full" result here shows that the agent correctly relays what the hired provider
returned for an invoice it had no prior answer for. It still isn't a reading of the PDF, because
the mocks replay keys.
