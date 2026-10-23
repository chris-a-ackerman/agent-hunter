# Agent-Hunter: Trial-then-Hire

A hiring service for AI agents: before a requester delegates an invoice-extraction job, we build a rubric
and a trial from their own task, run it on candidate provider agents, score them with confidence intervals,
and recommend one. MIT AI Studio (MAS.665), Fall 2026 — Chris Ackerman & Nathalie Abou Harb.

The full plan and source of truth is [PLAN.md](PLAN.md).

**Setup:** install [uv](https://docs.astral.sh/uv/), then run `uv sync` and `cp .env.example .env`
(fill in your Parley key). Run tests with `uv run pytest` and lint with `uv run ruff check`.
