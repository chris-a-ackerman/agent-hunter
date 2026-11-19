# Agent-Hunter: interface for calling agents

This page is for an AI agent (or its developer) that wants to use Agent-Hunter. It describes the
A2A door in `core/a2a.py` as the code stands today, and nothing else. Where something is
missing or rough, it says so.

## 1. What it offers

Agent-Hunter runs **trial-then-hire for invoice extraction**. You send a job posting (what you
want extracted, the JSON schema you want back, up to 3 sample invoices with their correct
answers, and your budgets). Agent-Hunter then:

1. reviews the posting (approve, reject, or ask for more information);
2. builds a scoring rubric and a trial from your samples;
3. picks the candidate provider agents that fit your job and budget;
4. sends every trial document to every candidate;
5. scores each output against the known answers, with 95% bootstrap confidence intervals;
6. ranks the candidates and recommends one, or reports a tie, or says there weren't enough.

You get back one **hire result**: the verdict (who to hire, scores, cost per document), which
candidates were tried or left out, and what the run cost.

What it does **not** do:

- It does not run your real job. The hire result names the recommended provider by ID
  (`slug@version`) only. It contains no endpoint URL, and there is no method to send your real
  invoices through Agent-Hunter to the hired provider. Reaching the provider is up to you.
- It does not accept uploaded documents. Sample paths point at files already on the server (§4.3).
- It does not return extracted invoices, the trial documents or the answer key.
- Only one job type: invoice extraction to JSON.

## 2. How to reach it

Default base URL: `http://127.0.0.1:8940/` (local only; see §6).

| What | How |
| -- | -- |
| Agent card | `GET /.well-known/agent-card.json` |
| Hire | `POST /`, JSON-RPC 2.0, method `message/send` |
| Fetch a past task | `POST /`, JSON-RPC 2.0, method `tasks/get`, `params: {"id": "<task id>"}` |

The agent card (actual output of the offline server):

```json
{"name":"agent-hunter","description":"Trial-then-Hire for invoice extraction: post a job, and Agent-Hunter tests candidate provider agents on it and recommends one, with confidence intervals.","url":"http://127.0.0.1:8940/","version":"0.1.0+017219a","capabilities":{"streaming":false,"pushNotifications":false},"defaultInputModes":["text"],"defaultOutputModes":["text"],"skills":[{"id":"trial_then_hire","name":"Trial then hire","description":"Trial-then-Hire for invoice extraction: post a job, and Agent-Hunter tests candidate provider agents on it and recommends one, with confidence intervals.","tags":["trial_then_hire","invoice_extraction"]}],"mode":"offline"}
```

`version` is `<project version>+<git short SHA>`. `mode` is `offline` or `live` (§5.6).

### message/send is synchronous

There is no streaming and no push notification. `message/send` holds the HTTP connection open
until the whole pipeline has finished, then returns a task whose state is already final. Offline
this took about 5–7 seconds per hire in our runs (one candidate always times out, which sets the
floor). Live it takes much longer; set a generous client timeout.

### tasks/get

Returns the stored task for any task ID this server has returned (tasks are saved as JSON files,
so they survive a restart). An unknown ID gets JSON-RPC error `-32001 "task not found"`.
`tasks/get` is only a re-read: by the time you have a task ID, the task is already final.

### JSON-RPC errors

Only envelope problems produce a JSON-RPC `error`:

| code | when |
| -- | -- |
| -32700 | body isn't JSON |
| -32600 | not an object, or `jsonrpc` isn't `"2.0"` |
| -32601 | method other than `message/send` or `tasks/get` |
| -32602 | `message/send` without a text part |
| -32001 | `tasks/get` for an unknown task ID |

Everything else, including a bad posting, comes back as a task (§4.1).

## 3. Required inputs

### 3.1 The message

`params.message.parts` must contain a part `{"kind": "text", "text": "<hire body as a JSON
string>"}`. Only the first text part is read. Other message fields (`role`, `messageId`) are
not checked.

### 3.2 The hire body

The text part is a JSON object:

| key | required by the code? | meaning |
| -- | -- | -- |
| `job` | **yes** | a JobPosting (§3.3) |
| `request_id` | no, but recommended | a string; makes the request idempotent (§5.5) |
| `nonce` | no | any value; it is part of the request's terms for dedupe, nothing else |

`request_id` and `nonce` come from the Nanda Town wire format; the door works without them, but
without a string `request_id` there is no dedupe and every retry starts a new job.

### 3.3 The JobPosting

Full schema: `contracts/schemas/JobPosting.json`. Unknown keys are rejected
(`additionalProperties: false`).

Required by the schema:

| field | type | notes |
| -- | -- | -- |
| `requester_id` | string | who you are; free text |
| `description` | string | what the job is |
| `output_description` | string | what each output should look like |
| `job_budget_usd` | number ≥ 0 | budget for the whole real job (`n_real_docs` documents) |
| `trial_budget_usd` | number ≥ 0 | budget for the trial (see §5.4 for what is enforced) |

Optional, but needed in practice:

| field | default | notes |
| -- | -- | -- |
| `output_schema` | `null` | a JSON Schema object. **Missing → `needs_info`.** Its `properties` must include `total`, `line_items`, `vendor_name`, `invoice_date`, `invoice_number`, `currency`, or the job is **rejected** |
| `samples` | `[]` | **at most 3**. Each is `{"path": "...", "answer": <Invoice> \| null}`. Offline, at least one sample must have an `answer`, or the run fails (§4.1) |
| `n_real_docs` | `15` | integer ≥ 1; size of your real batch. Per-document budget = `job_budget_usd / n_real_docs` |
| `deadline` | `null` | `YYYY-MM-DD` |
| `deadline_open_ack` | `false` | set `true` if there is no deadline; otherwise the live Job Reviewer asks for one (`needs_info`) |
| `region` | `null` | if set, only candidates whose card region matches or is `"global"` are tried |
| `min_reputation` | `null` | unused |
| `allow_unrated` | `true` | |
| `sourcing_mode` | `"registry"` | |

An `answer` is an Invoice: `vendor_name` (string), `invoice_number` (string or null),
`invoice_date` (`YYYY-MM-DD`), `currency` (ISO 4217), `line_items` (list of `{description,
quantity, unit_price, amount}`), `subtotal` (number or null), `tax` (number or null), `total`
(number). All eight keys are required, no extras.

**Sample paths are server-side.** `path` is resolved on the Agent-Hunter host, relative to its
`SAMPLES_DIR` (by default the repo's `examples/` folder). You cannot upload a file. A path that
doesn't exist there makes the run fail with `FileNotFoundError` (§4.1). The files that exist
on a default checkout are:

```
samples/invoice_clean/sample_{1,2,3}.pdf   short tables, clean digital PDFs
samples/invoice_long/sample_{1,2,3}.pdf
samples/invoice_mixed/sample_{1,2,3}.pdf
```

Their correct answers are in `examples/invoice_clean.json`, `invoice_long.json` and
`invoice_mixed.json` on the server.

## 4. Returned outputs

### 4.1 Task states

| state | when | where the details are |
| -- | -- | -- |
| `completed` | the pipeline finished: ranked, rejected, needs_info or paused | one artifact, `hire_result` (§4.2) |
| `failed` | the text isn't a hire request | status message `{"error": "invalid_request", "detail": "..."}` |
| `failed` | the pipeline crashed (e.g. missing sample file, no sample with an answer offline) | status message `{"error": "pipeline_error", "detail": "job <id> stopped: <ExceptionClass>"}` |
| `failed` | a retry of a request that was running when the server restarted | status message `{"error": "interrupted", ...}` |
| `rejected` | same `request_id` reused with different terms | status message `{"error": "conflicting_request", ...}` |

A rejected or needs_info **job** is still a `completed` **task**; read `job_status` and `reason`
in the hire result. Crash details are limited to the exception class name.

### 4.2 The hire result

The `hire_result` artifact has one text part: the hire result as JSON with sorted keys. Parse
the string.

| field | type | meaning |
| -- | -- | -- |
| `request_id` | as sent, or `null` | echoed |
| `mode` | `"offline"` \| `"live"` | which pipeline ran |
| `job_id` | string | Agent-Hunter's job ID (`job-<hex>`) |
| `job_status` | string | `ranked`, `rejected`, `needs_info` or `paused` |
| `reason` | string \| null | why the job stopped short; `null` when `ranked` |
| `verdict` | object \| null | `null` unless `ranked` (below) |
| `spend_usd.platform_usd` | number | Agent-Hunter's own model spend for this job (always 0 offline) |
| `spend_usd.providers_usd` | number | sum of what the candidates reported spending on the trial |
| `spend_usd.total_usd` | number | the two added |
| `candidates` | object, **only if the Candidate Finder ran** | absent for a job stopped earlier (e.g. rejected, needs_info) |
| `candidates.considered` | list of provider IDs | the candidates that were sent the trial |
| `candidates.excluded` | list of `{id, reason}` | listed providers left out, and why (e.g. price over budget); then any card that couldn't be listed, by card URL |

`verdict` (schema: `contracts/schemas/Verdict.json`):

| field | type | meaning |
| -- | -- | -- |
| `status` | `recommended` \| `tie` \| `insufficient_candidates` | `recommended`: the leader's CI doesn't overlap the runner-up's. `tie`: CIs overlap. `insufficient_candidates`: fewer than 2 providers answered |
| `recommended` | provider ID \| null | the pick; for a tie, the tie-break winner (lowest cost per doc, then lowest latency); `null` for `insufficient_candidates` |
| `tie` | bool | true exactly when `status` is `tie` |
| `tied` | list of provider IDs | the overlapping group when `tie`, else empty |
| `trial_accuracy` | number in [0, 1] \| null | the recommended provider's trial score |
| `ci_95` | `[low, high]` \| null | its 95% bootstrap CI |
| `cost_per_doc` | number \| null | its mean reported cost per trial document, USD |
| `valid_until` | date \| null | |
| `ranking` | list | one row per candidate scored, best first: `provider`, `accuracy`, `ci_95`, `cost_per_doc`, `timeouts`, `mean_latency_ms` |
| `rationale` | string | one-paragraph explanation (a template offline) |
| `evidence` | string | the trial ID the verdict rests on (the trial itself is not returned) |

A provider ID is `slug@version`. It is a name, not an address (§1).

## 5. Permissions and limits

1. **No authentication.** Anyone who can reach the port can hire. The server binds to
   `127.0.0.1` by default; don't expose it.
2. **Invoice extraction only.** Postings whose `output_schema` lacks the six scored fields are
   rejected; candidates must declare job type `invoice_extraction`.
3. **At most 3 samples, server-side paths only** (§3.3). Offline, the trial *is* your samples
   (those with answers), so a trial has 1–3 documents. With so few documents the confidence
   intervals are very narrow or zero-width (e.g. `[1.0, 1.0]`): treat them as weak evidence.
4. **Budgets.** `job_budget_usd / n_real_docs` is the per-document budget; candidates whose card
   list price is above it are excluded (with the reason in `candidates.excluded`). If that leaves
   fewer than 2, the verdict is `insufficient_candidates`. `trial_budget_usd` is checked only to
   be ≥ 0; nothing in the code caps trial spend by it. Candidates are offered their own list
   price per document. Live, Agent-Hunter's own model calls are also under a monthly platform
   budget (`MONTHLY_BUDGET_USD`); a provider answering `402 budget_exhausted` pauses the run
   (`job_status: "paused"`).
5. **Dedupe on `request_id`.** Terms = the whole hire body minus `request_id` (so `nonce` counts).
   - first request: runs the job;
   - identical retry: gets the stored reply back byte-for-byte (same task ID, same job), no new
     job, no new spend; a retry that arrives mid-run waits for that run;
   - same `request_id`, different terms: `rejected` task, `conflicting_request`.
   The mapping is stored on disk, so it survives a restart. To run the same posting again on
   purpose, use a new `request_id`.
6. **One job at a time (recommended, not enforced).** There is no queue and no global lock: only
   requests with the same `request_id` are serialized. We sent two different hires at once
   offline and both completed, but concurrent runs share the same doc server and provider
   endpoints and aren't tested beyond that. Send one hire, wait for its reply, then the next.
7. **Offline vs live.**
   - *Offline* (`--offline` or `AGENT_HUNTER_OFFLINE=1`): no Parley key, no model calls (any model
     call raises). Stub Job Reviewer, Rubric Builder, Trial Builder and Ranker; the real Candidate
     Finder, Trial Runner and Scorer. The candidates are five **mock providers** (`mock-a@1` …
     `mock-e@1`) that the server starts on `MOCK_PROVIDERS_BASE_URL` (default
     `http://localhost:8101`). The mocks never read the PDF: they answer from the stored answer
     key and corrupt fields at fixed rates, so their scores are synthetic. One of them always
     times out and one always returns non-JSON. The mocks therefore **cannot extract an invoice
     they have no answer key for**; offline mode demonstrates the hiring flow, not real
     extraction.
   - *Live* (the default without `--offline`): needs a `.env` with Parley credentials; the real
     agents (LLM-backed) and the real provider registry (`PROVIDERS_BASE_URL`,
     `PROVIDER_SLUGS`, `PROVIDER_A2A_URLS`). It costs real money. This page was not checked
     against a live run.
8. **Storage.** Jobs, dedupe records (`a2a_requests/`) and tasks (`a2a_tasks/`) go to the job
   store: `STORE_DIR` if set, else `.data/offline-store` offline or `.data/store` live. Note that
   the repo's `.env.example` sets `STORE_DIR=.data/store`, so with a `.env` copied from it,
   offline jobs land in `.data/store` too. Set `STORE_DIR` explicitly to keep runs apart.
9. **No cancel, no list.** There is no method to cancel a running hire or to list past tasks.

## 6. Setup

From the repo root (Python 3.12, [uv](https://docs.astral.sh/uv/)):

```bash
uv sync
uv run python -m core.a2a --offline --port 8940
# Agent-Hunter A2A door (offline): http://127.0.0.1:8940 (card at /.well-known/agent-card.json)
```

This also starts, in the same process, the doc server (`DOCS_BASE_URL`, default
`http://localhost:8200`) and the mock providers (default `http://localhost:8101`), unless
something already answers there. Options: `--host` (default `127.0.0.1`), `--port` (default
8940). `A2A_PUBLIC_URL` overrides the `url` on the card.

Check it is up:

```bash
curl -s http://127.0.0.1:8940/.well-known/agent-card.json
```

## 7. Example

Run against the offline server above on 2026-11-18 (commit `017219a`). One sample, minimal
schema.

The hire body:

```json
{
  "request_id": "example-0001",
  "nonce": "7f3a9c",
  "job": {
    "requester_id": "ap-assistant",
    "description": "Extract vendor, invoice number, date, currency, line items and total from supplier invoices.",
    "output_description": "One JSON object per invoice, matching output_schema.",
    "output_schema": {
      "type": "object",
      "properties": {
        "vendor_name": {"type": "string"},
        "invoice_number": {"type": ["string", "null"]},
        "invoice_date": {"type": "string"},
        "currency": {"type": "string"},
        "line_items": {"type": "array"},
        "subtotal": {"type": ["number", "null"]},
        "tax": {"type": ["number", "null"]},
        "total": {"type": "number"}
      }
    },
    "samples": [
      {
        "path": "samples/invoice_clean/sample_2.pdf",
        "answer": {
          "vendor_name": "Warner, Williamson and Dickerson",
          "invoice_number": "INV-42405",
          "invoice_date": "2026-05-04",
          "currency": "USD",
          "line_items": [
            {"description": "Courier delivery, same day", "quantity": 24, "unit_price": 50.05, "amount": 1201.2},
            {"description": "Ballpoint pens, box of 50", "quantity": 9, "unit_price": 17.52, "amount": 157.68},
            {"description": "Training workshop, per attendee", "quantity": 25, "unit_price": 500.67, "amount": 12516.75}
          ],
          "subtotal": 13875.63,
          "tax": 1110.05,
          "total": 14985.68
        }
      }
    ],
    "n_real_docs": 50,
    "job_budget_usd": 1.0,
    "trial_budget_usd": 0.1,
    "deadline_open_ack": true
  }
}
```

The request: the hire body serialized to a string, inside a JSON-RPC envelope.

```bash
BODY=$(python3 -c 'import json,sys; print(json.dumps(json.dumps(json.load(open(sys.argv[1])))))' body.json)
curl -s -X POST http://127.0.0.1:8940/ -H 'content-type: application/json' --data "{
  \"jsonrpc\": \"2.0\", \"id\": 1, \"method\": \"message/send\",
  \"params\": {\"message\": {\"kind\": \"message\", \"role\": \"user\", \"messageId\": \"msg-0001\",
    \"parts\": [{\"kind\": \"text\", \"text\": $BODY}]}}}"
```

The actual response, verbatim:

```json
{"jsonrpc":"2.0","id":1,"result":{"kind":"task","id":"task-991297dbaf91","contextId":"ctx-3d85a854f877","status":{"state":"completed"},"artifacts":[{"artifactId":"hire-result","name":"hire_result","parts":[{"kind":"text","text":"{\"candidates\":{\"considered\":[\"mock-a@1\",\"mock-b@1\",\"mock-c@1\",\"mock-d@1\",\"mock-e@1\"],\"excluded\":[]},\"job_id\":\"job-539d0b6cbe82\",\"job_status\":\"ranked\",\"mode\":\"offline\",\"reason\":null,\"request_id\":\"example-0001\",\"spend_usd\":{\"platform_usd\":0.0,\"providers_usd\":0.006500000000000001,\"total_usd\":0.006500000000000001},\"verdict\":{\"ci_95\":[1.0,1.0],\"cost_per_doc\":0.004,\"evidence\":\"trial-4efc2bc627da\",\"ranking\":[{\"accuracy\":1.0,\"ci_95\":[1.0,1.0],\"cost_per_doc\":0.004,\"mean_latency_ms\":2.0,\"provider\":\"mock-a@1\",\"timeouts\":0},{\"accuracy\":0.7999999999999999,\"ci_95\":[0.7999999999999999,0.7999999999999999],\"cost_per_doc\":0.001,\"mean_latency_ms\":4.0,\"provider\":\"mock-b@1\",\"timeouts\":0},{\"accuracy\":0.5,\"ci_95\":[0.5,0.5],\"cost_per_doc\":0.0005,\"mean_latency_ms\":3.0,\"provider\":\"mock-c@1\",\"timeouts\":0},{\"accuracy\":0.0,\"ci_95\":[0.0,0.0],\"cost_per_doc\":0.0,\"mean_latency_ms\":null,\"provider\":\"mock-d@1\",\"timeouts\":1},{\"accuracy\":0.0,\"ci_95\":[0.0,0.0],\"cost_per_doc\":0.001,\"mean_latency_ms\":5.0,\"provider\":\"mock-e@1\",\"timeouts\":0}],\"rationale\":\"mock-a@1 leads and its confidence interval clears the runner-up's.\",\"recommended\":\"mock-a@1\",\"status\":\"recommended\",\"tie\":false,\"tied\":[],\"trial_accuracy\":1.0,\"valid_until\":null}}"}]}]}}
```

The `hire_result` text part, parsed:

```json
{
  "candidates": {
    "considered": ["mock-a@1", "mock-b@1", "mock-c@1", "mock-d@1", "mock-e@1"],
    "excluded": []
  },
  "job_id": "job-539d0b6cbe82",
  "job_status": "ranked",
  "mode": "offline",
  "reason": null,
  "request_id": "example-0001",
  "spend_usd": {
    "platform_usd": 0.0,
    "providers_usd": 0.006500000000000001,
    "total_usd": 0.006500000000000001
  },
  "verdict": {
    "ci_95": [1.0, 1.0],
    "cost_per_doc": 0.004,
    "evidence": "trial-4efc2bc627da",
    "ranking": [
      {"accuracy": 1.0, "ci_95": [1.0, 1.0], "cost_per_doc": 0.004, "mean_latency_ms": 2.0, "provider": "mock-a@1", "timeouts": 0},
      {"accuracy": 0.7999999999999999, "ci_95": [0.7999999999999999, 0.7999999999999999], "cost_per_doc": 0.001, "mean_latency_ms": 4.0, "provider": "mock-b@1", "timeouts": 0},
      {"accuracy": 0.5, "ci_95": [0.5, 0.5], "cost_per_doc": 0.0005, "mean_latency_ms": 3.0, "provider": "mock-c@1", "timeouts": 0},
      {"accuracy": 0.0, "ci_95": [0.0, 0.0], "cost_per_doc": 0.0, "mean_latency_ms": null, "provider": "mock-d@1", "timeouts": 1},
      {"accuracy": 0.0, "ci_95": [0.0, 0.0], "cost_per_doc": 0.001, "mean_latency_ms": 5.0, "provider": "mock-e@1", "timeouts": 0}
    ],
    "rationale": "mock-a@1 leads and its confidence interval clears the runner-up's.",
    "recommended": "mock-a@1",
    "status": "recommended",
    "tie": false,
    "tied": [],
    "trial_accuracy": 1.0,
    "valid_until": null
  }
}
```

Reading it: `mock-a@1` is recommended, scoring 1.0 on a one-document trial (hence the zero-width
CI) at $0.004 per document. Sending the same envelope again returns the same task
(`task-991297dbaf91`) without running a new job; `tasks/get` with that ID returns it too.

Other replies we saw on the same server:

- posting without `output_schema` → `completed`, `job_status: "needs_info"`,
  `reason: "The posting has no output schema."`, no `candidates`, `verdict: null`;
- `job_budget_usd: 0.01` (with `n_real_docs: 15`) → `ranked` with every mock in
  `candidates.excluded` (`"price $0.0100/doc exceeds the budget $0.0007/doc"`, …) and verdict
  `insufficient_candidates`;
- a sample with no `answer` → `failed`, `pipeline_error`, `ValueError`;
- a sample path that doesn't exist → `failed`, `pipeline_error`, `FileNotFoundError`;
- `"job": {}` → `failed`, `invalid_request`,
  `"\"job\" is not a valid JobPosting (5 errors; first at requester_id)"`;
- the example's `request_id` reused with a different body → `rejected`, `conflicting_request`.
