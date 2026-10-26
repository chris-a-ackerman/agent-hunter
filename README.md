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
