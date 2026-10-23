# Agent-Hunter: Trial-then-Hire — Project Overview & Technical Plan

**Team:** Chris Ackerman, Nathalie Abou Harb · **Course:** MIT AI Studio (MAS.665), Fall 2026
**Version:** v1.7, Oct 22, 2026 · **Source spec:** *HW5 Proposal: Trial-then-Hire Protocol for Agents*
**Key dates:** **Core MVP due Wed Oct 28** · Demo materials due **Thu Nov 19** · Demo Day **Thu Dec 3** (2–3 min live demo)

---

## 0. TL;DR

- We build a **hiring service for agents**. A human posts an invoice-extraction job. Our agents turn it into a scoring rubric and a small trial. Seven provider agents attempt the trial, we score them, and the human hires the one we recommend.
- **Job family is locked:** invoice extraction to a JSON schema.
- **Stack:** Python core service (agents, trial runner, scorer, MCP server), a Next.js requester UI, and Supabase for the database, file storage, live updates and auth. Providers run as a separate service in a separate repo, so their hidden traits stay hidden.
- **All models through MIT Parley** ($30/month of free API usage each). Platform agents and cheap providers run on GPT-6 Luna ($0.10 per million input tokens); only "strong" providers use GPT-6 Sol or Claude Sonnet 5.5. Estimated total spend is about $55–75 across the project, which fits the allowance as long as the big experiment is split across both accounts and the Dec 1 reset (§12).
- **MVP (due Wed Oct 28) is the core pipeline only, with no UI.** A job posting goes in as JSON through an API or script. It flows through review → rubric → trial → 7 providers → scoring → ranking, and a verdict comes out. The web UI comes after the MVP.
- **Build order:** core MVP (6 days) → week-1 risk check on real data → UI + hire flow + deploy → build as much as possible by the Nov 19 submission → final experiment, polished deck and Demo Day (Dec 3).
- **Async-first.** There are two workstreams that only touch through a shared `contracts` package, and each side has mocks of the other.

---

## 1. Project overview

### 1.1 Problem
Registries and agent cards tell an agent **who exists** and what they *claim*. Nothing tells an agent **who is actually good at this specific job** before it delegates. Reputation systems rate agents only after a job, and benchmarks test generic tasks, not the requester's own task.

### 1.2 What we build
**Trial-then-Hire** is a selection service for jobs whose results can be scored automatically. Before a requester commits to a provider, we:
1. Check that the job can be scored (if not, we reject it or ask for details).
2. Build a rubric and a trial that look like the requester's real task.
3. Run the trial on several candidate providers, through the same channel they would use for real work.
4. Score the attempts, attach confidence intervals, rank the providers and recommend one (or report a tie).
5. After the hire, record how the real job went in a reputation ledger (final version).

**One-liner:** *Registries tell an agent who exists; we tell it who is actually good at this job.*

### 1.3 Job family (locked)
**Invoice extraction to JSON.** The input is an invoice (PDF or image). The output is:

```json
{
  "vendor_name": "string",
  "invoice_number": "string | null",
  "invoice_date": "YYYY-MM-DD",
  "currency": "ISO 4217 code, e.g. USD",
  "line_items": [
    {"description": "string", "quantity": 0, "unit_price": 0.0, "amount": 0.0}
  ],
  "subtotal": "number | null",
  "tax": "number | null",
  "total": 0.0
}
```

We chose this family because it is scored objectively (no LLM judge needed), public labeled data exists, and providers differ in real ways: digital vs. scanned input, and long line-item tables.

### 1.4 User flow
*In the MVP, step 1 is a JSON job posting submitted to `POST /jobs` (or the `run_job` script). The flow ends at the verdict in step 8; hiring is a single API call at most. The web form and the hire screen come after the MVP.*

1. **A human creates a job posting** (web app after the MVP): description, desired output, optional sample invoices with answers, job budget, trial budget, deadline and region.
2. **Job Reviewer** (our agent) approves the job, asks for more details, or rejects it as not scorable.
3. **Rubric Builder** drafts field weights and checks. *This is auto-approved in the MVP.*
4. **Trial Builder** assembles the trial: requester samples, synthetic invoices with known answers, and real invoices from a trial-only pool.
5. **Eval Builder** binds the rubric to the trial's answer key.
6. **Candidate Finder** picks the providers from our registry.
7. **Trial Runner** sends each provider the trial tasks through its normal endpoint.
8. **Scorer** grades every output. **Ranker** produces a ranking, a recommendation and a plain-English rationale.
9. **The human picks a provider and "hires" it.** Payment is simulated.

The final version adds: the hired provider runs the real job, a spot-check measures real accuracy (`report_outcome`), a ledger entry is written, and re-trials trigger on drift. Requester *agents* can also do all of this through the MCP server.

### 1.5 What's out of scope (MVP)
The web UI, deployment (the MVP can run locally), real held-out data and the week-1 study, real payments, human rubric editing, inbound provider applications, signed requests, NANDA registry integration, a subjective LLM judge, and multiple job families.

### 1.6 Success criteria (from the proposal)
| Check | Target |
|---|---|
| Selection quality | On 40 real jobs, the recommended provider reaches **≥ 90%** of the accuracy of the best provider in hindsight |
| Comparison | (a) LLM ranks provider cards vs. (b) trial-then-hire vs. (c) trial + ledger, measured on real-job accuracy, cost per successful job (trial cost included) and time to hire. Plot (b) vs. (c) as the ledger fills |
| Representativeness | Trial accuracy correlates with real-job accuracy. A classifier can't tell trial tasks from real ones (≤ 60% accuracy) |
| Sandbagger | Its ledger score falls below every honest provider's within 10 jobs |
| Fake requester | Only signed outcome reports from the trial's own requester are accepted |

---

## 2. Decisions log

| # | Decision | Choice | Date |
|---|---|---|---|
| D1 | Job family | Invoice extraction to JSON schema | Oct 22 |
| D2 | MVP vs. final | MVP = intake → rubric → trial → 7 providers → scored ranking (hire via UI comes after the MVP). Final = ledger, `report_outcome`, re-trials, sandbagger test, 40-job comparison | Oct 22 |
| D3 | Week-1 risk check | Kept: trial-vs-real correlation and a trial-vs-real classifier | Oct 22 |
| D4 | NANDA | References stay in the spec, but NANDA is **not a requirement**. MVP discovery uses our own registry table. NANDA Index/AgentFacts publishing is a stretch goal | Oct 22 |
| D5 | Requester | A human creates the job in a web UI. Our agents build the rubric and trial. Providers' agents take the trial. Our agent scores. The human chooses whom to hire | Oct 22 |
| D6 | Rubric approval | Auto-approved in the MVP | Oct 22 |
| D7 | Payments / trial fees | Simulated (recorded as numbers) in the MVP | Oct 22 |
| D8 | Backend | Supabase (Postgres, Storage, Realtime, Auth) | Oct 22 |
| D9 | Model budget | Originally a $50 cap; now the Parley allowance ($30 per account per month), with the budget guard at $25 per account per month (see D16) | Oct 22 |
| D10 | Way of working | Most code written by Claude Code. Async-first. Tasks tracked in Linear | Oct 22 |
| D13 | MVP deadline | **Wed Oct 28**: prove the core system works end to end | Oct 22 |
| D14 | UI priority | The UI is not part of the MVP. The core pipeline is driven by API/script | Oct 22 |
| D15 | Hosting | All on Vercel (Hobby, free) under Chris's account. Collaborate on GitHub; Chris handles deployments. No Railway/Render | Oct 22 |
| D16 | Model credits | MIT Parley API: $30/month each for grad students ($60/month combined), refreshed on the 1st. Default split: Chris's key for providers, Nathalie's for platform + eval; both keys share provider load during the 40-job experiment. Spend is tracked by component in `llm_calls`, not by key | Oct 22 |
| D11 | Language and stack | Python core (FastAPI, Pydantic), Next.js UI (§4). Agreed | Oct 22 |
| D12 | Build split | Two workstreams (§11): Chris = platform + providers, Nathalie = evaluation + selection. Agreed | Oct 22 |

---

## 3. Canonical component list

The proposal has two lists of agents that don't match (§4 vs. §5 of the spec). This table merges them into one. It keeps the flow names and notes the spec's other name for each.

| # | Component | Spec alias | Type | What it does | Phase |
|---|---|---|---|---|---|
| 1 | **Job Reviewer** | Intake agent | LLM agent | Checks the posting is complete and scorable. Approves, asks follow-ups, or rejects. Outputs a structured **JobSpec** | MVP |
| 2 | **Rubric Builder** | Eval designer | LLM agent | Proposes field weights, required fields, tolerances and consistency checks (e.g. line items sum to the total). Outputs a machine-readable **Rubric** | MVP (auto-approve) |
| 3 | **Trial Builder** | Trial builder | LLM agent + code | Picks and generates the trial documents (requester samples + synthetic + trial-pool real), matched to the requester's documents. Outputs a **TrialPackage** with an answer key | MVP |
| 4 | **Eval Builder** | — | Code | Joins the Rubric and the TrialPackage answer key into a scoring config. *Recommendation: code, not an LLM. For invoices this step is deterministic, and an LLM adds cost and risk for no gain* | MVP |
| 5 | **Candidate Finder** | Candidate sourcing | Code | Reads the provider registry and filters by declared job type, price, region and (later) reputation threshold. Keeps slots for newcomers | MVP |
| 6 | **Trial Runner** | Trial runner | Code | Sends tasks to providers' own endpoints, in parallel, through the same channel as real jobs. Enforces timeouts and records cost and latency | MVP |
| 7 | **Scorer** | Scorer | Code | Field-level weighted scoring with bootstrap confidence intervals. A timeout scores 0 | MVP |
| 8 | **Ranker** | Ranker agent | Code + LLM | Code ranks the providers and detects ties. An LLM writes a 2–3 sentence rationale for the human. Says "insufficient candidates" if fewer than 2 respond | MVP |
| 9 | **Ledger** | Ledger | Code | Append-only records per provider *version*: trial scores, real outcomes, verdict expiry, drift → re-trial | MVP: trial scores only · Final: full |
| 10 | **Post-mortem** | Post_mortem | Code (+LLM summary) | After a hire, spot-checks the real job and writes `report_outcome` to the ledger | Final |
| 11 | **Candidate Reviewer** | Candidate_reviewer | Code | Handles inbound `apply(job_id)` requests: checks job type, region and reputation threshold | Final |
| 12 | **Judge** | Judge | LLM agent | Grades subjective outputs against a rubric. *Not needed for invoices*, kept for a second job family | Stretch |
| 13 | **Card-ranking baseline** | Comparison (a) | LLM | Ranks providers from their agent cards alone, for the comparison | Final |
| 14 | **Provider agents ×7** | — | Separate service | The candidates being hired. Built with hidden traits | MVP |

The proposal's MVP step 7 ("Scorer confirms the reputation score") moves to the final version, because the MVP has no ledger to check against.

---

## 4. Architecture & tech stack

### 4.1 System diagram

```
                 ┌──────────────────────────────┐
  Human ───────► │  Requester Web App (Next.js) │  create job · live progress · ranking · hire
                 └──────────────┬───────────────┘
                                │ supabase-js (row-level security)
                 ┌──────────────▼───────────────┐
                 │           Supabase           │  Postgres (state, ledger) · Storage (invoices)
                 │                              │  Realtime (progress) · Auth
                 └──────────────▲───────────────┘
                                │ service role
┌───────────────────────────────┴────────────────────────────────┐
│                 Core Service (Python, FastAPI)                 │
│  Pipeline runner (state machine, one run per job)              │
│   Job Reviewer → Rubric Builder → Trial Builder → Eval Builder │
│   → Candidate Finder → Trial Runner → Scorer → Ranker          │
│  Ledger · Post-mortem (final)                                  │
│  MCP server (final): create_job, get_job, select_provider,     │
│   get_trial_result, hire, report_outcome, apply                │
│  Model gateway → MIT Parley: one place for calls + cost logs   │
└───────────────────────────────┬────────────────────────────────┘
                                │ HTTPS, same contract for trials and real jobs
┌───────────────────────────────▼────────────────────────────────┐
│     Provider Service (separate repo, separate deploy)          │
│   /p/{slug}/.well-known/agent-card.json  · POST /p/{slug}/tasks│
│   7 providers, each with its own card, price and model setup   │
└────────────────────────────────────────────────────────────────┘
```

### 4.2 Stack choices

| Layer | Choice | Why |
|---|---|---|
| Core language | **Python 3.12** + FastAPI + Pydantic | Best ecosystem for PDFs/images, scoring statistics and the MCP/A2A SDKs. Pydantic models double as our contracts |
| UI (post-MVP) | **Next.js on Vercel** + supabase-js | Native Supabase fit. Realtime progress for the live demo |
| MVP entry point | FastAPI `POST /jobs`, `GET /jobs/{id}` + a `run_job` CLI script | Proves the core works with no UI. Becomes the backend the UI and MCP server call later |
| Data | **Supabase** Postgres + Storage + Realtime + Auth | Chris knows it well. Gives us live updates for free |
| Pipeline execution | MVP: runs in-process locally. Deployed: one Vercel Function per job run (the full pipeline takes well under Hobby's 300 s limit); move to Vercel Workflows (Python supported) if runs get longer or need retries | No always-on worker to host. Job state lives in Supabase either way |
| Model access | **MIT Parley API**: OpenAI-compatible, base URL `https://parley.api.mit.edu/v1`, `Bearer sk-parley-v1-…` keys. One endpoint serves Claude, GPT, Gemini and Llama models. Use the OpenAI Python SDK (or LiteLLM in OpenAI mode) pointed at that URL | No per-provider adapters. Parley returns each request's cost in an `x-parley-v1-cost` header, so the gateway logs real cost rather than estimates. Parley retries 429/503/504 itself (up to 2 retries) |
| Platform agents' model | **GPT-6 Luna** (default) | $0.10 / $0.50 per million input/output tokens, with native JSON output (`response_format`). Upgrade one agent to GPT-6 Sol or Sonnet 5.5 only if its quality fails |
| Provider models | Mix across families and tiers from Parley's list, e.g. GPT-6 Luna, GPT-5.4 nano, Gemini 3.5 Flash-Lite, Gemini 3.8 Flash, Llama 4 Maverick (free), with GPT-6 Sol / Claude Sonnet 5.5 for the strong tier. *Exact assignment lives in the private providers repo* | Makes "strong vs. cheap" a real difference. Parley lists image input for the GPT, Claude and Gemini models; image support for Llama 4 Maverick isn't documented, so test it first or use it only for a text-layer provider |
| Invoice input to models | Render each page to **PNG and send it inline as base64** (`image_url` data URL) | Works for the GPT, Claude and Gemini models through Parley. Claude accepts images only as base64. OpenAI models don't take PDFs inline, and Parley's file upload has a 20-file, 48-hour limit |
| JSON output | Native `response_format` on GPT and Gemini. For Claude it's best-effort, so use a tool whose input schema is the invoice schema, and **validate every output** against the schema | Parley doesn't check Claude replies against the schema |
| PDF/image handling | `pypdf`/`pdfplumber` for the text layer, `pdf2image` for page images | Lets some providers use text extraction and others vision, which creates the specialist's trait naturally |
| Synthetic invoices | Faker + Jinja2 HTML templates → WeasyPrint (PDF) → PNG; `augraphy` for a scanned look | The answer key is correct by construction, and we can generate many layouts |
| Hosting (everything) | **Vercel Hobby (free)**: three projects (core API, providers, UI), all on Chris's account. No Railway/Render | Chris knows Vercel. FastAPI runs as Python Vercel Functions. Provider calls are short, stateless requests, a natural fit. Hobby is for non-commercial personal use, which a class project is. Collaboration happens on GitHub; **Chris deploys**. On Hobby, commits to a private repo by anyone other than the account owner don't trigger deployments, so Nathalie's pushes won't auto-deploy and her PRs get no preview builds. Chris deploys after merging (CLI or a re-push) |
| MCP server | Official Python MCP SDK (FastMCP), streamable HTTP | The "clear interface for agents" the course asks for |
| Terminal output (MVP) | `rich` live timeline + ranking table in `run_job` | The Oct 28 video is a screen recording of the terminal, so it has to read well |
| Tests | pytest. Fixture invoices with known answers | Claude Code works best with tests to aim at |

### 4.3 Repos
Use **two repos**. Hidden traits are part of the experiment's validity, and a single monorepo would leak them.

```
agent-hunter/                 # platform + evaluation (both of you)
  contracts/                  # Pydantic models + JSON Schemas: the ONLY shared surface
  core/
    pipeline/                 # state machine + worker
    agents/                   # job_reviewer, rubric_builder, trial_builder, ranker
    eval/                     # eval_builder, scorer, stats (bootstrap CI)
    runner/                   # trial runner, candidate finder
    ledger/
    mcp/
  apps/web/                   # Next.js requester UI
  data/                       # loaders, pool manifests (no raw data in git)
  experiments/                # week-1 study, 40-job comparison, plots
  mocks/                      # fake providers / fake scorer for async work
  supabase/migrations/

agent-hunter-providers/       # Chris only: traits live here, Nathalie never opens it
  providers/{slug}/           # card.json, pipeline, model config
  service.py                  # one FastAPI app serving all 7 under /p/{slug}
```

---

## 5. Data model & contracts

### 5.1 Contracts (the `contracts/` package)
`JobPosting`, `JobSpec`, `Rubric`, `TrialPackage`, `ProviderTask`, `ProviderResult`, `ScoreReport`, `Verdict`, `OutcomeReport`, `AgentCard`.

The **Verdict** follows the format in the proposal:

```json
{"recommended": "provider-slug@v3", "trial_accuracy": 0.97, "ci_95": [0.93, 0.99],
 "cost_per_doc": 0.004, "valid_until": "2026-12-01", "tie": false,
 "ranking": [{"provider": "...", "accuracy": 0.97, "ci_95": [0.93, 0.99], "cost_per_doc": 0.004, "timeouts": 0}],
 "rationale": "plain-English 2–3 sentences", "evidence": "trial-8841"}
```

### 5.2 Supabase tables
| Table | Key columns |
|---|---|
| `jobs` | id, requester_id, description, output_description, schema_json, job_budget, trial_budget, deadline, deadline_open_ack, min_reputation, allow_unrated, sourcing_mode, region, **status** |
| `job_documents` | id, job_id, storage_path, role (`sample`/`real`), ground_truth_json |
| `job_specs` | job_id, spec_json, decision (`approve`/`needs_info`/`reject`), notes |
| `rubrics` | id, job_id, rubric_json, approved_by (`auto` in MVP), approved_at |
| `providers` / `provider_versions` | slug, endpoint, agent_card_json, declared_job_types, price_per_doc, region · version, created_at |
| `trials` | id, job_id, rubric_id, status, cost_cap, started_at, finished_at |
| `trial_tasks` | id, trial_id, document_ref, source (`sample`/`synthetic`/`trial_real`), ground_truth_json |
| `submissions` | id, trial_task_id, provider_version_id, output_json, latency_ms, cost_usd, status (`ok`/`timeout`/`error`) |
| `scores` | submission_id, field_scores_json, doc_score |
| `verdicts` | trial_id, verdict_json, recommended_provider_version_id, tie, valid_until |
| `hires` | id, job_id, trial_id, provider_version_id, simulated_amount, hired_at |
| `ledger_entries` | **append-only**: provider_version_id, job_type, trial_score, real_outcome, requester_id, signature, created_at |
| `llm_calls` | component, model, tokens_in, tokens_out, cost_usd, job_id, created_at (powers the budget guard) |

**Job status machine:** `submitted → reviewing → (needs_info | rejected) → rubric_ready → trial_built → running → scored → ranked → hired → (final) outcome_reported`

### 5.3 Document pools
To keep trial documents and real-job documents apart, we use three pools:

| Pool | Used by | Who can see it |
|---|---|---|
| `requester_samples` | Trial Builder (as anchors) | Everyone |
| `trial_real` | Trial Builder (mixed into trials so they look real) | Trial Builder |
| `job_real` (held out) | Real jobs and the experiment harness only | **Never the Trial Builder.** Enforced in code + manifest |

Synthetic invoices are generated per job to match the requester samples.

---

## 6. Datasets

| Use | Source | Notes |
|---|---|---|
| Real invoices (primary) | **DocILE**: 6,680 manually annotated real business documents (invoice-like), with key-field and line-item annotations, distributed as PDFs + JSON. It also includes a 100k-document synthetic subset | Best fit: real invoices, line items, many layouts. **Token received (Oct 22).** Test the download early; the MVP doesn't need it |
| Fallback real | Receipt datasets with line items (e.g. CORD) or totals (e.g. SROIE) | Simpler documents. Only good enough to unblock |
| Synthetic | Our generator (§4.2). DocILE's synthetic set as a second source | The answer key is correct by construction |

**MVP data (Oct 28):** don't wait on DocILE. The MVP runs on synthetic invoices from our generator. That covers the requester samples, trial documents and a small "real job" set rendered from different templates. Real held-out data comes in with the week-1 study right after the MVP.

**Make the jobs different from each other.** If one provider wins every job, selection becomes trivial and the comparison shows nothing. Define 4 job "types" and spread the 40 jobs across them:
1. Clean digital PDFs, short tables
2. Scanned or photographed invoices
3. Long line-item tables (15+ rows)
4. Non-English or non-USD invoices

These give the specialist provider a real strength and a real weakness. They also let a cheap provider win easy jobs on cost, which is where trial-then-hire should beat "always pick the strongest card".

---

## 7. Provider population

**This section deliberately leaves out which provider has which trait.** That mapping lives only in `agent-hunter-providers/`.

The population matches the proposal: strong generalist, cheap generalist, specialist, overclaimer, underclaimer, sandbagger and flaky.

Rules for building providers:
- Every provider publishes an ordinary **agent card** (name, description, skills/job types, price per document, region, version) and a `POST /tasks` endpoint.
- **No `is_trial` flag exists anywhere** in the task payload, headers or timing. The sandbagger must *guess* which tasks are trials from whatever it can observe (document look, requester history, pacing). Our defence is to make that guessing fail.
- Traits come from real differences where possible (model tier, text-layer vs. vision, prompt quality, timeouts), not from hard-coded scores.
- Each provider logs its own model spend separately from platform spend.

---

## 8. Scoring & selection

**Per-document score.** A weighted sum of field scores. Invalid JSON or a schema failure scores 0. A timeout scores 0.

| Field | How it's matched | Default weight |
|---|---|---|
| total | Numeric, within ±0.01 | 0.30 |
| line_items | Predicted items matched to true items (Hungarian matching on description similarity), then quantity, unit price and amount compared. F1 over items | 0.30 |
| vendor_name | Normalized fuzzy match ≥ 0.9 | 0.15 |
| invoice_date | Exact match after normalizing to ISO | 0.15 |
| invoice_number | Exact match after normalizing | 0.05 |
| currency | Exact match | 0.05 |

The Rubric Builder may re-weight these per job (weights must sum to 1). A **sum check** (line items ≈ subtotal/total) is reported as a consistency flag next to the score.

**Trial score.** The mean document score, with a **95% CI from bootstrap resampling** over documents (1,000 resamples).

**Ranking.** Sort providers by trial score. Recommend the leader only if its CI doesn't overlap the runner-up's. Otherwise report a **tie** and break it by cost per document, then latency. If fewer than 2 providers answer, return "insufficient candidates" instead of a recommendation.

**Honest caveat.** With about 10 trial documents, the CIs will be wide and ties will be common. Two levers:
- **Adaptive trials:** give every provider 6 documents, then 6 more only to the top 3. This narrows the CIs where it matters and cuts cost.
- **Trial size:** we report how trial size affects CI width and cost as an experiment result in its own right.

**Verdict validity (final).** A verdict lasts 30 days or until the provider's version changes. A real-outcome drop below the trial CI triggers a re-trial.

---

## 9. Interfaces

### 9.0 MVP entry point (no UI)
- `POST /jobs` takes a `JobPosting` JSON (the same fields as the form below) and returns a `job_id`. `GET /jobs/{id}` returns the status, rubric, per-provider scores and Verdict.
- `python -m core.run_job examples/<job>.json` submits a job, follows it to completion and prints the step timeline, ranking table, cost and runtime.
- `examples/` holds at least three postings: two scorable invoice jobs (different invoice types) and one unscorable job that the Job Reviewer should reject.

### 9.1 Requester web app (post-MVP)
- **New job form:** description; desired output (pre-filled invoice schema); upload sample invoices + expected JSON (optional); job budget; trial budget ($0 / compute cost / max); deadline (or open-ended, with an acknowledgement); region. The reputation threshold and sourcing mode appear but are disabled in the MVP.
- **Job page:** a pipeline timeline with live status per step; the rubric (read-only); a live trial grid (provider × document); the ranking table with CI bars, cost and timeouts; the rationale; and a **Hire** button that creates a simulated contract.

### 9.2 Provider contract (HTTP; identical for trials and real jobs)
- `GET /p/{slug}/.well-known/agent-card.json`
- `POST /p/{slug}/tasks` with `{task_id, requester_id, document_url, output_schema, deadline_ms, offered_price}` → `{task_id, output, cost_usd, provider_version}`
- Default timeout: 60 s. Trial tasks are spread out over time like real jobs, not burst-sent.

### 9.3 MCP server (final; MVP stretch)
Requester tools: `create_job(job_details) → job_id` · `get_job(job_id)` · `select_provider(job_id) → trial_id` · `get_trial_result(trial_id) → Verdict` · `hire(trial_id, provider)` · `report_outcome(trial_id, measured_accuracy)`.
Provider tool: `apply(job_id)`.
Trials are asynchronous: callers poll `get_trial_result`, or pass a callback URL.

### 9.4 Identity (final)
Requesters and providers register Ed25519 public keys and sign their calls. `report_outcome` is accepted only from the trial's own requester, and only once. Providers can read their ledger record but not write to it. In the MVP, API keys are enough.

---

## 10. Milestones (MVP → final)

Each milestone ends on a Thursday class day.

| Milestone | Dates | Scope | Exit criterion |
|---|---|---|---|
| **M1 Core MVP** | Thu Oct 22 – **Wed Oct 28** | Contracts; Supabase schema; LLM gateway + cost log + budget guard; synthetic invoice generator; Job Reviewer, Rubric Builder (auto-approve), Trial Builder, Eval Builder, Candidate Finder, Trial Runner, Scorer (+ bootstrap CI), Ranker; 7 providers (thin wrappers are fine); pipeline worker; `POST /jobs` + `run_job` script. Runs locally | The 3 example postings run end to end: 1 rejected by the Job Reviewer, 2 return a Verdict with all 7 providers scored, CIs, the recommendation, cost and runtime logged. **Submitted:** 1-minute video + paragraph |
| **M2 Risk check + real data** | Thu Oct 29 – Thu Nov 5 | DocILE (or fallback) loaders + pools; **week-1 study**: all providers run on ~50 synthetic + ~50 real documents, we measure the rank correlation between trial and real accuracy, and train a trial-vs-real classifier; harden providers' traits; adaptive trials | Study write-up with a go/no-go. If the classifier beats 60%, fix the generator first |
| **M3 Product layer + deploy** | Fri Nov 6 – Thu Nov 12 | Web UI (create → live progress → ranking → hire); hire flow; deploy core + providers + UI | A human posts a job on the deployed app and gets a recommendation in **under ~2 minutes** |
| **M4 Build push + Nov 19 submission** | Fri Nov 13 – Thu Nov 19 | **Build as much as possible**: hired provider runs the real job + automatic spot-check; ledger v1; MCP server (`create_job`, `get_job`, `get_trial_result`); agent-facing docs; start the 40-job experiment if the rest is done. Draft deck; project URL | **Nov 19 submission:** title, team list, project URL, deck draft, booth needs |
| **M5 Final + polished deck** | Nov 20 – Dec 2 | 40-job comparison (a/b/c) + plots; **polished deck** built around the results; sandbagger defence test; signed outcome reports + fake-requester test; re-trial on drift; `apply()` + Candidate Reviewer; demo script, rehearsal and a backup recording. *Nov 26 is Thanksgiving, so plan around it* | Results in hand and the demo rehearsed twice |
| **Demo Day** | Dec 3 | 2–3 min live demo | — |

### 10.1 Core MVP day-by-day (Thu Oct 22 → Wed Oct 28)

| Day | Joint | Workstream P (Chris) | Workstream E (Nathalie) |
|---|---|---|---|
| **Thu Oct 22** | Repo + `contracts` v0 (JobPosting, JobSpec, Rubric, TrialPackage, ProviderTask/Result, ScoreReport, Verdict) | Supabase project + MVP tables (`jobs`, `job_specs`, `rubrics`, `trials`, `trial_tasks`, `submissions`, `scores`, `verdicts`, `providers`, `llm_calls`) | Synthetic generator: first template + answer key |
| **Fri Oct 23** | **Contracts frozen by end of day** | LLM gateway + cost log + budget guard; core FastAPI + worker skeleton; provider service + first 2 providers | Generator v1 (3+ layouts, PDF + PNG); Scorer v1 with tests; `mocks/fake_providers` |
| **Sat–Sun Oct 24–25** | — | Providers 3–7; Trial Runner (parallel, timeouts, cost capture); Candidate Finder; Job Reviewer | Rubric Builder; Trial Builder; Eval Builder; bootstrap CI + tie logic; Ranker |
| **Mon Oct 26** | **Integration day:** wire the full pipeline. Run on mocks first, then real providers. Fix contract mismatches | | |
| **Tue Oct 27** | Run the 3 example jobs; fix; record the 1-minute video (§10.2); finalize the paragraph; short README. Rest is buffer | | |
| **Wed Oct 28** | **Submit:** video + paragraph | | |

**MVP cut line if behind:**
- Candidate Finder returns all 7 providers.
- Rubric Builder starts from default weights and the LLM only adjusts them.
- Providers 5–7 can be prompt/model variants of the others.
- If Supabase setup slows you down, the worker writes to local JSON behind a small storage interface, and you switch to Supabase after the MVP.

**MVP never-cut:**
- A real HTTP call between the core and each provider
- The Scorer with CIs
- The Job Reviewer's reject path
- The cost log

**Cut order if behind (after the MVP).** Cut from the top first:
1. `apply()` / Candidate Reviewer
2. Signed requests
3. Automatic drift re-trials (run manually instead)
4. MCP beyond read-only tools

**Never cut:** the 40-job comparison and the sandbagger result. They are the story.

### 10.2 Oct 28 submission: 1-minute video + one paragraph

**What this means for the build.** With no UI, the video *is* the terminal. That means `run_job` has to be readable on screen:
- A live step-by-step timeline (✓ Reviewed → ✓ Rubric → ✓ Trial built → providers finishing one by one).
- A final ranking table: provider, accuracy, 95% CI, cost/doc, timeouts, with the recommended provider highlighted.
- A one-line summary: total cost and runtime.

Use `rich` (Python) for this. It's a small task, not a UI build. Size the demo trial so the whole run takes **under ~45 s**, or record it at real speed and speed up the middle in editing.

**Shot list (~60 s):**
| Time | On screen | Voiceover (Chris or Nathalie) |
|---|---|---|
| 0:00–0:08 | Title card: "Trial-then-Hire: test agents on your job before you hire one" | "Registries tell an agent who exists. We tell it who's actually good at this job." |
| 0:08–0:15 | The JSON job posting (invoice extraction, 3 sample invoices) | "A requester posts an invoice-extraction job." |
| 0:15–0:22 | `run_job` on the unscorable example → **Rejected by Job Reviewer** | "Jobs we can't score automatically are rejected up front." |
| 0:22–0:45 | `run_job` on the real example. The timeline fills in, then 7 providers report back | "Our agents build a rubric and a trial with known answers, then seven independent provider agents take it over HTTP." |
| 0:45–0:55 | The ranking table, CIs, the highlighted recommendation and rationale, cost + runtime | "We score every field, rank with confidence intervals, and recommend one, all for under a cent per document." |
| 0:55–1:00 | "Next: real-invoice validation, a reputation ledger, catching sandbaggers" | "Next, we check that trial scores predict real-job performance." |

Fill the voiceover's "under a cent" and runtime claims from the actual run before recording.

**Paragraph (draft; fill in the brackets from the real run):**
> Agent-Hunter's Trial-then-Hire is a hiring service for AI agents. Before a requester agent delegates a job, our service tests several candidate providers on that requester's own task, then recommends the best one with evidence. Our MVP runs the full core pipeline end to end for invoice extraction to JSON. A Job Reviewer agent checks the posting and rejects jobs that can't be scored. A Rubric Builder sets field weights. A Trial Builder assembles a trial of invoices with known answers. Seven independent provider agents, with hidden strengths and weaknesses we built in, attempt the trial through their own HTTP endpoints. Our Scorer grades each extracted field, computes 95% confidence intervals, and ranks the providers, reporting a tie when the intervals overlap. In the demo, the service screened [N] providers on [N] invoices in [X] seconds for $[X] total, and recommended [provider], which scored [X]% accuracy. Next, we will check that trial scores predict performance on held-out real invoices, add a reputation ledger, and test whether the system catches a provider that does well on trials but cuts corners on real work.

### 10.3 Demo Day script (2–3 min, draft)
1. **0:00** The problem in one sentence.
2. **0:20** Post a live invoice job in the app.
3. **0:40** Watch the trial grid fill in live. This needs a trial that finishes in under 60 s: small trial, parallel calls, warmed-up services.
4. **1:30** The ranking with confidence intervals, a tie broken on cost, then Hire.
5. **2:00** One chart from the 40-job experiment: (b) and (c) beat card-ranking, and the ledger catches the sandbagger.

Keep a recorded fallback ready in case the live run fails.

---

## 11. Working asynchronously

### 11.1 Workstreams (proposed; replaces the split in spec §6)
| Workstream | Owner | Owns |
|---|---|---|
| **P: Platform & Providers** | Chris | Supabase schema, pipeline worker, Job Reviewer, Candidate Finder, Trial Runner, Ledger, Post-mortem, web UI, MCP server, deploys, **all 7 providers** |
| **E: Evaluation & Selection** | Nathalie | Datasets + pools, synthetic generator, Rubric Builder, Trial Builder, Eval Builder, **Scorer, Ranker**, week-1 study, 40-job analysis, sandbagger detection, card-ranking baseline |

**Why the Ranker moves to Nathalie:** whoever builds the providers knows their traits. That person must not build anything that judges them. Chris builds the providers, so Nathalie owns scoring *and* selection, and Chris doesn't edit `core/eval/` or the Ranker.

### 11.2 Rules that keep you unblocked
1. **Contracts first.** Everything crosses through `contracts/`. Any change goes through a PR with a `contract-change` label that the other person approves.
2. **Mocks on day one.** Workstream E gets `mocks/fake_providers` (fixed outputs, known accuracy). Workstream P gets `mocks/fake_scorer` (random but valid ScoreReports). Neither side waits on the other.
3. **One Linear issue = one Claude Code session.** Each issue carries its acceptance test. Claude Code reads the issue, the contracts and this plan.
4. **Short weekly sync** after Thursday class. Everything else goes through Linear comments.
5. **The budget guard is shared.** Every model call goes through the gateway. The guard refuses calls once spend passes the cap.

---

## 12. Budget (MIT Parley allowance)

**What we have.** MIT Parley gives staff and graduate students a **$30 monthly subsidy** (undergraduates get $10), replenished on the 1st of each month. With two of us that's **$60/month**, or about $150–180 across Oct 22–Dec 3. For graduate students, the only way to keep going after a month's credit runs out is to attach an MIT cost object, so treat **$30 per account per month as a hard ceiling**: once it's spent, calls return `402` until the 1st. Set the gateway's budget guard to **$25 per account per month** to keep a buffer for demos.

**Parley rates for the models we'd use** (per million input/output tokens, Parley's rates as of Sep 29, 2026; no markup over list price):

| Tier | Model | In / Out | ≈ Cost per invoice* |
|---|---|---|---|
| Free | Llama 4 Maverick | $0 / $0 up to a monthly free allowance | $0 |
| Cheap | **GPT-6 Luna** | $0.10 / $0.50 | ~$0.0007 |
| Cheap | GPT-5.4 nano | $0.20 / $1.25 | ~$0.0015 |
| Cheap | Gemini 3.5 Flash-Lite | $0.30 / $2.50 | ~$0.0026 |
| Mid | Gemini 3.8 Flash (intro rate through Dec 31) | $0.75 / $3.75 | ~$0.005 |
| Mid | Claude Haiku 4.5 | $1.00 / $5.00 | ~$0.0065 |
| Strong | GPT-6 Sol | $2.00 / $10.00 | ~$0.013 + reasoning |
| Strong | Claude Sonnet 5.5 (always thinks) | $2.00 / $10.00 | ~$0.02–0.03 |

*About 3,500 input tokens (page image + prompt) and 600 output tokens, plus thinking tokens for Sonnet 5.5.

Claude Haiku 5.5 isn't on Parley, and Haiku 4.5 costs 10× more, so **GPT-6 Luna is our cheap default** (the same price Haiku 5.5 would have been). Llama 4 Maverick is free, which makes it the model for plumbing tests and debugging. Parley's docs list no batch API, so there's no batch discount.

**Estimated spend** (assuming 2 strong providers and 5 cheap or mid ones, about $0.008 per provider call on average):

| Activity | Calls | Est. cost | When |
|---|---|---|---|
| Development and debugging (use Llama 4 Maverick where possible) | — | ~$5–8 | Oct–Nov |
| Week-1 risk study (7 providers × 100 docs) | ~700 | ~$6 | Oct 29–Nov 5 |
| Platform agents (reviewer, rubric, trial builder, ranker rationale on GPT-6 Luna) | ~500 | ~$1 | Throughout |
| 40-job experiment: 7 providers × (12 trial + 15 real docs) × 40 jobs, with outputs cached by (provider version, document) and shared across arms a/b/c | ~5,000–7,500 | ~$40–60 | Nov 20–Dec 2 |
| Demo runs and rehearsals | — | ~$2 | Nov 19, Dec 1–3 |
| **Total** | | **~$55–75** | |

That fits the overall allowance, but **the 40-job experiment won't fit in one account's month**. Plan for it:
1. **Split provider calls across both keys during the experiment.** Every call is tagged by component in `llm_calls`, so provider and platform spend stay separate even when they share a key.
2. **Straddle the Dec 1 reset.** Run part of the experiment Nov 20–30 and finish it Dec 1–2 on the new month's credit.
3. **Cache aggressively** and re-use real documents across jobs, so a provider never processes the same document twice.

**Rate limits:** 90 requests and 1M tokens per minute, per account. A live demo trial (7 providers × ~6 docs ≈ 42 calls) fits easily. The full experiment takes about 1–1.5 hours of calls on one key, under an hour on two.

**Free tiers and credits (checked Oct 22):**
| Service | What's free | Watch out for |
|---|---|---|
| **MIT Parley API (primary)** | **$30/month each** (graduate students), refreshed on the 1st. OpenAI-compatible API at `https://parley.api.mit.edu/v1` covering Claude, GPT and Gemini models | Hard cap per month for grad students unless a cost object is added. Separate project accounts also need a cost object |
| Vercel Hobby | Hosting, 1M function invocations/mo, 4 active-CPU hrs/mo (waiting on model calls doesn't count) | Non-commercial only; 300 s max per function; Nathalie's commits to a private repo won't auto-deploy |
| Supabase Free | 2 projects, 500 MB database, 1 GB file storage, 5 GB egress/mo | **Pauses after 1 week of inactivity** (watch Thanksgiving week); restore from the dashboard |
| Claude API | $5 sign-up credit. Max and Team subscribers get monthly API credits | Backup only if Parley runs dry |
| AWS / Google Cloud new accounts | ~$300 credit each, and both serve Claude (Bedrock / Vertex) | Card required; credits expire. Backup only |

**Levers if spend runs high:** fewer real documents per job (15 → 10); adaptive trials (only the top 3 providers get the second round); run strong providers on GPT-6 Sol instead of Sonnet 5.5, since Sonnet 5.5's thinking can't be turned off; use Llama 4 Maverick for one of the cheap providers.

---

## 13. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Synthetic trials are easier or harder than real jobs, so trial scores don't predict | The risk-check study (M2) gates the UI build and the 40-job experiment; mix trial-pool real documents into trials; match layouts to requester samples |
| Providers can tell trials apart (the sandbagger wins) | Same channel, pacing and requester ID for trials and jobs; trial-vs-real classifier as a check; ledger spot-checks as the backstop |
| CIs too wide, so everything is a tie | Adaptive trials; cost tie-break; report trial size vs. CI width |
| One provider wins every job, so the comparison is flat | 4 heterogeneous job types; measure cost per successful job, not just accuracy |
| DocILE download fails or the data is messier than expected | Token is in hand; test the download in M1, not M2. The MVP doesn't need it (synthetic only). Fallback: CORD receipts |
| 6-day MVP slips | Contracts frozen Fri; mocks on both sides; Monday is integration-only; MVP cut line in §10.1 |
| Hidden traits leak | Separate providers repo; Chris doesn't edit eval/ranker code |
| Live demo is slow or flaky | Small trial, parallel calls, warmed services, recorded fallback |
| Overspend, or running out of Parley credit mid-month (`402` errors until the 1st) | Budget guard at $25 per account per month; Llama 4 Maverick for debugging; caching; split the experiment across both keys and the Dec 1 reset; the runner treats `402` as "pause", not as a provider failure |
| Code written by Claude Code drifts from the spec | Contracts + tests in every issue; this plan in the repo as `PLAN.md` |

---

## 14. Open questions

All of the original questions are answered (Oct 22):

| # | Question | Answer |
|---|---|---|
| 1 | What must work on Nov 19 vs. Dec 3? | Unknown. **Build as much as possible by Nov 19**; submit the required items then (title, team list, project URL, deck draft, booth needs). **Polish the deck for Dec 3** |
| 2 | Does Nathalie agree to the two-workstream split? | **Yes** (D12) |
| 3 | Is a real job a batch of ~15 invoices? | **Yes** |
| 4 | Hosting? | **All on Vercel** (D15) |
| 5 | DocILE license and access? | **Token received**, download not yet tested. Test it in M1 (see §15) |

Nothing open right now. New questions get added here.

---

## 15. Linear issue breakdown (ready to create)

Labels: `ws:platform`, `ws:eval`, `contract-change`, `stretch`. Milestones as in §10.

**M1 Core MVP (due Wed Oct 28)**
- [P/E] Repo + `PLAN.md` + `contracts` v0 (all models in §5.1 + JSON Schemas). *Joint, first. Freeze Fri Oct 23*
- [P] Supabase project + MVP tables (or a local JSON store behind an interface)
- [P/E] Both get MIT Parley API keys from platform.parley.mit.edu → My Keys; set a monthly budget limit on each account *(today)*
- [P] Model gateway: OpenAI SDK pointed at Parley, cost from the `x-parley-v1-cost` header into `llm_calls`, budget guard ($25 per account per month), `402` handling
- [P] Core FastAPI (`POST /jobs`, `GET /jobs/{id}`) + pipeline runner + status machine
- [P] Provider service skeleton + agent cards + `POST /tasks`
- [P] Providers 1–7 (traits per private spec; thin variants OK for the MVP)
- [P] Job Reviewer agent + JobSpec + reject path
- [P] Candidate Finder (registry filters; may return all 7)
- [P] Trial Runner (parallel, timeouts, cost capture)
- [P] `mocks/fake_scorer`
- [E] Synthetic invoice generator (3+ layouts, PDF/PNG, answer key)
- [E] Test the DocILE download with our token (annotated-trainval split); confirm the file format. *Not on the MVP's critical path, but do it this week*
- [E] Scorer: field matchers, Hungarian line-item matching, weights, sum check (with tests)
- [E] Bootstrap CI + tie logic
- [E] Rubric Builder agent (auto-approve)
- [E] Trial Builder agent (samples + synthetic)
- [E] Eval Builder (code)
- [E] Ranker (ranking + LLM rationale + insufficient-candidates path)
- [E] `mocks/fake_providers`
- [P/E] Integration: `run_job` script + 3 example postings run end to end (Mon Oct 26)
- [P] `run_job` terminal output with `rich`: live timeline + ranking table + cost/runtime line (demo run under ~45 s)
- [P/E] Record the 1-minute video per the §10.2 shot list; fill in the paragraph from the real run; short README (Tue Oct 27)
- [P/E] Submit video + paragraph (Wed Oct 28)

**M2 Risk check + real data**
- [E] DocILE loaders; `trial_real` / `job_real` pool manifests (fallback: CORD)
- [E] Week-1 study: correlation + trial-vs-real classifier + write-up (go/no-go)
- [E] Adaptive trials (6 docs to all → 6 more to top 3)
- [P] Harden provider traits on real data; scan-noise inputs

**M3 Product layer + deploy**
- [P] Web UI: new job form
- [P] Web UI: job page with live timeline, trial grid, ranking, Hire
- [P] Hire flow (simulated contract)
- [P] Deploy core API + providers + UI as three Vercel projects; warm-up script before demos (cold starts)
- [P] End-to-end smoke test on the deployed URL (< 2 min)

**M4 Build push + Nov 19 submission**
- [P] Run hired job + automatic spot-check → ledger v1
- [P] MCP server: `create_job`, `get_job`, `get_trial_result` + agent docs
- [P/E] Deck draft + project URL + Nov 19 submission form

**M5 Final**
- [E] Card-ranking baseline (a)
- [E] 40-job experiment harness (a/b/c), caching, spread across both keys and the Dec 1 reset, plots
- [E] Sandbagger detection analysis
- [P] Signed requests + `report_outcome` rules + fake-requester test
- [P] Verdict validity + drift re-trial
- [P] `apply()` + Candidate Reviewer `stretch`
- [P/E] Polished deck for Dec 3 (results charts, demo flow)
- [P/E] Demo script, 2 rehearsals, backup recording

---

### Sources
- Course page: https://aiforimpact.github.io/fall26.html (dates, deliverables)
- DocILE benchmark: https://arxiv.org/abs/2302.05658 · https://github.com/rossumai/docile
- MIT Parley API docs (MIT sign-in required): https://parley-docs.mit.edu/ (models, cost guidance, rate limiting, file attachments, providers, accounts)
- MIT Parley announcement: https://orgchart.mit.edu/letters/parley-api-now-available-mit-community
- Vercel Functions limits: https://vercel.com/docs/functions/limitations · Hobby plan: https://vercel.com/docs/plans/hobby

*Prices were checked Oct 22, 2026. Re-check them before the large experiment runs.*
