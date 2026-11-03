# Agent-Hunter: Trial-then-Hire

A hiring service for AI agents: before a requester delegates an invoice-extraction job, we build a rubric
and a trial from their own task, run it on candidate provider agents, score them with confidence intervals,
and recommend one. MIT AI Studio (MAS.665), Fall 2026 — Chris Ackerman & Nathalie Abou Harb.

The full plan and source of truth is [PLAN.md](PLAN.md).

**Setup:** install [uv](https://docs.astral.sh/uv/), then run `uv sync` and `cp .env.example .env`
(fill in your Parley key). Run tests with `uv run pytest` and lint with `uv run ruff check`.
Run uv from inside the repo, and don't copy or move `.venv`. If `uv run pytest` fails with
`.venv/bin/python: No such file or directory`, a script in `.venv/bin` points at an old venv
path; `rm -rf .venv && uv sync` fixes it.

**Synthetic invoices** (`data/synth`) render PDFs with WeasyPrint, which needs the pango system
library: `brew install pango` on macOS, `apt-get install libpango-1.0-0 libpangoft2-1.0-0` on
Debian/Ubuntu. On Apple Silicon, `uv run` doesn't pass the library path through, so run the
rendering tests with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest`.
Without pango, rendering tests are skipped and only answer keys can be generated:
`uv run python -m data.synth --layout classic --n 20 --seed 1 [--line-items 15 30] [--no-render]`.

**Demo web app** (AGE-32): `uv run python -m core.web`, then open http://localhost:8300. Pick an
example job and click **Run trial**: each pipeline step checks off live with what it produced, the
providers' lanes fill as answers arrive, and the ranking and recommendation follow. It runs the real
pipeline in-process, like `run_job`, and starts the doc server on `DOCS_BASE_URL` if nothing is
listening there. The real providers (`agent-hunter-providers`, port 8100) must be running, unless
you switch on **Mock providers**. Opened as a file, or with `?demo`, `apps/web/index.html` plays a
fixture run with placeholder data instead (`?moment=idle|running|finished|rejected`,
`?theme=light`, `?speed=2`).

## HW7 benchmark

The harness (`experiments/hw7/run_benchmark.py`, plan: `experiments/hw7/PREREG.md`) runs, per job, the
real system on a 12-invoice trial (twice), the profile-reading baseline 5× and the cheapest pick, and
sends the 12 held-out invoices to all 7 providers for ground truth. Everything lands in
`experiments/hw7/runs/<run_id>/` (commit it): `submissions.jsonl` (one row per provider × invoice,
with its output, doc and field scores, latency and cost), `picks.jsonl`, `llm_calls.jsonl`,
`trials/` (doc IDs only) and `summary.json` (spend, wall clock, failures by job type, commits and
settings).

1. **Inputs.** The postings and samples of the synthetic jobs are committed; the held-out invoices
   (`data/docs`, `data/truth`) and the two real-invoice jobs j02 and j09 are not. Download DocILE
   (https://github.com/rossumai/docile, research use only) into `data/docile/`, then build them all:
   `uv run python -m experiments.hw7.make_jobs`. The harness stops with an error if any are missing.
2. **Providers service** (the private repo, on its own Parley key), at the frozen cards' commit:
   ```bash
   cd ../agent-hunter-providers && git checkout 5d9536a && uv run uvicorn service:app --port 8100
   ```
3. **Platform key.** In this repo's `.env`, `PARLEY_API_KEY` and `PARLEY_ACCOUNT=nathalie` (the review,
   rubric, trial-builder and baseline calls run on Nathalie's account; the harness refuses another
   `PARLEY_ACCOUNT` unless you pass `--platform-account`). `PROVIDERS_BASE_URL`, `PROVIDER_SLUGS`,
   `DOCS_BASE_URL`, `TASK_DEADLINE_MS=60000` and `MAX_CONCURRENCY=24` as in `.env.example`. The harness
   starts the doc server on `DOCS_BASE_URL` itself.
4. **Run** (on Apple Silicon, the trial PDFs need pango on the library path, see above):
   ```bash
   export DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib
   uv run python -m experiments.hw7.run_benchmark --mock --only j01,j02 --repeat 2   # dry run, no cost
   uv run python -m experiments.hw7.run_benchmark --only j01 --repeat 1 --run-id j01-check
   uv run python -m experiments.hw7.run_benchmark --jobs tuning --repeat 2 --max-spend-usd 12 --run-id tuning
   ```
   Over `--max-spend-usd` (providers' `cost_usd` + platform model cost) no new batch starts and the exit
   code is 2; a provider's 402 or the gateway's budget guard stops the run with exit code 3. Rerun with the
   same `--run-id` to resume: pairs and picks already logged are skipped. Held-out answers already in any
   earlier run are copied (`cached_from`), never resent. Rubrics are built once per job and saved to
   `experiments/hw7/rubrics/` (commit them).
