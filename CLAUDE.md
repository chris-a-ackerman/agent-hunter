# CLAUDE.md — agent-hunter

## Project
Agent-Hunter: Trial-then-Hire is a hiring service for AI agents (MIT AI Studio, Fall 2026; Chris Ackerman
and Nathalie Abou Harb). A requester posts an invoice-extraction-to-JSON job; our agents review it, build a
rubric and a trial with known answers, send the trial to 7 provider agents over HTTP, score every output
with bootstrap CIs, rank them and recommend one (or report a tie). Python 3.12 core (FastAPI, Pydantic),
Supabase for state, Next.js UI after the MVP.

**PLAN.md is the source of truth.** When an issue cites a section (e.g. "plan §5.1"), read that section
of PLAN.md before starting. Don't edit PLAN.md unless the issue asks for it.

## How we work
- Tasks are Linear issues in team **AGE** (e.g. AGE-5). **One issue = one session.** Read the issue first.
- The issue's acceptance criteria are the definition of done. Don't expand scope beyond the issue.
- Branch: the Linear branch name if the issue gives one, otherwise `<user>/age-<n>-<slug>`.
- Put the issue ID in every commit message and PR title (e.g. `feat: scorer field matchers (AGE-12)`)
  so Linear links them.

## Workstreams (PLAN.md §11)
- **Chris — P: Platform & Providers:** Supabase schema, pipeline, Job Reviewer, Candidate Finder, Trial
  Runner, ledger, MCP server, web UI, deploys, all 7 providers (in the other repo).
- **Nathalie — E: Evaluation & Selection:** data/pools, synthetic generator, Rubric Builder, Trial Builder,
  Eval Builder, Scorer, Ranker, experiments.
- Don't edit the other workstream's folders unless the issue says so. In particular, Chris never edits
  `core/eval/` or the Ranker (he knows the provider traits).

## Contracts rule
`contracts/` is the only shared surface between workstreams. After the freeze (**Fri Oct 23**), any change
needs a PR labelled `contract-change` and approval from the other workstream. Never change a contract
silently to make your own code pass — fix your code, or open a contract-change PR.

## Hidden-traits rule
Which provider has which trait must **never** appear in this repo: not in code, comments, tests, fixtures,
commits, PRs or Linear. Providers live in the separate private repo `agent-hunter-providers`. Code here
treats every provider as an opaque endpoint + agent card.

## Model calls
- Every LLM call goes through the model gateway (once it exists) — no direct SDK calls elsewhere.
- MIT Parley, OpenAI-compatible; base URL and key from env (`PARLEY_BASE_URL`, `PARLEY_API_KEY`).
- Default model `gpt-6-luna`. Use `llama-4-maverick-17b` (free) for tests and debugging.
- Hard budget: $30/month per account (guard at `MONTHLY_BUDGET_USD=25`). Never write loops that call
  models without an explicit cap on calls or spend.

## Secrets
Never commit keys or `.env` (only `.env.example`). Never print keys or auth headers in logs or errors.

## Tests
- pytest. Prefer mocks from `mocks/` over live model calls in unit tests.
- Live calls only in tests marked `@pytest.mark.live`; they are skipped by default.
- Add or update tests with every change; CI must stay green.

## Commands
```bash
uv sync                      # setup (then: cp .env.example .env and fill it in)
uv run pytest                # tests (live tests excluded)
uv run pytest -m live        # live tests only — spends Parley credit
uv run ruff check            # lint
uv run ruff format           # format
uv add <pkg>                 # runtime dep (root);  uv add --package agent-hunter-contracts <pkg> for contracts
```

## Layout (PLAN.md §4.3)
`contracts/` (package `agent-hunter-contracts`, import `contracts`) · `core/{pipeline,agents,eval,runner,
ledger,mcp}` · `apps/web/` (post-MVP) · `data/` (loaders + manifests, no raw data in git) ·
`experiments/` · `mocks/` · `supabase/migrations/` · `examples/` (job postings) · `tests/`

## Key dates
- **Wed Oct 28:** core MVP due — no UI, runs locally (PLAN.md §10.1).
- **Thu Nov 19:** submission (project URL, deck draft).
- **Thu Dec 3:** Demo Day.
