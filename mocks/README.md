# mocks

Stand-ins for real components, so each workstream can build and test before the other's
pieces exist (plan §11.2 rule 2).

## fake_providers (AGE-18)

Five fake providers on one local HTTP server, speaking the same provider contract as the real
provider service (plan §9.2): `GET /p/{slug}/.well-known/agent-card.json` and
`POST /p/{slug}/tasks`.

    uv run python -m mocks.fake_providers --port 8101 [--truth-dir data/truth] [--fail-budget]

For `run_job --mock`, set `MOCK_PROVIDERS_BASE_URL=http://localhost:8101` and
`MOCK_PROVIDER_SLUGS=mock-a,mock-b,mock-c,mock-d,mock-e`.

**Ground truth (D17):** the mock takes `doc_id` from the task's `document_url`
(`.../d/{doc_id}.pdf`) and reads `{TRUTH_DIR}/{doc_id}.json` (default `data/truth`). It never
downloads the PDF. Only mocks may read `data/truth/`; real providers never can.

### Known accuracy

For each of the six scored fields independently, with probability `1 − p` the value is replaced
with a clearly wrong one, so that field scores 0. The expected weighted doc score is then `p`,
whatever the rubric weights.

| slug | behaviour | p | `cost_usd` | card `price_per_doc_usd` |
| -- | -- | -- | -- | -- |
| `mock-a` | corrupt | 0.95 | 0.004 | 0.010 |
| `mock-b` | corrupt | 0.80 | 0.001 | 0.003 |
| `mock-c` | corrupt | 0.60 | 0.0005 | 0.002 |
| `mock-d` | sleeps 2 × `deadline_ms`, then 504 (always times out) | — | — | 0.005 |
| `mock-e` | 200 with `output = {"text": "not json"}` (parses as a `ProviderResult`, isn't an `Invoice`) | 0 | 0.001 | 0.004 |

Expected statuses: `mock-d` → submission `timeout`; `mock-e` → submission `ok`, doc score
`invalid` (0).

**Wrong values:** `total` × 1.1 (1.00 if zero) · `vendor_name` → a different Faker company ·
`invoice_date` + 37 days · `invoice_number` → a random string (also when null) · `currency` →
a different ISO code · `line_items` → every item's `unit_price` and `amount` × 1.5 (F1 = 0).
`subtotal` and `tax` are left alone (they aren't scored).

**Deterministic:** decisions use `random.Random(f"{slug}:{doc_id}")`, so the same document always
gets the same answer. The model is importable without the server:
`from mocks.fake_providers import mock_output, corrupt`.

**Tests:** `tests/test_fake_providers.py` checks that over 5000 generated answer keys, each
mock's per-field corruption rate is within ±2 points of `1 − p`. (At 500 keys a single
field's spread is up to ±2.2 points, so ±2 would fail by chance.)

`--fail-budget` makes every task return `402 {"error": "budget_exhausted"}` (D18), for testing
the Trial Runner's pause.

Cards: `version = "1"` (provider ID `mock-x@1`), `job_types = ["invoice_extraction"]`,
`region = "global"`.

## fake_scorer (AGE-16)

`mocks/fake_scorer.py` has the same `score(config, submissions) -> list[ScoreReport]` as the real
Scorer (`core/eval/scorer.py`, AGE-19); both satisfy `contracts.steps.Score`. The scores are made up
but schema-valid:

- One `ScoreReport` per provider (first-appearance order), one `DocScore` per submission.
- Per provider, `random.Random(sha256(trial_id:provider))` draws a base accuracy in 0.5–0.98; each
  doc scores base ± 0.1, clipped to [0, 1]. `field_scores` repeats the doc score for each weight
  key; `sum_check_ok = None`.
- Non-`ok` submissions (and `ok` ones whose output isn't an `Invoice` → `invalid`) score 0.
- `trial_score` = mean doc score; `ci_95` = percentile bootstrap, 200 resamples, same seed.
- Same inputs → identical output. Imports nothing from `core/eval/`.

**Switch:** `SCORER=mock|real` (default `mock`), read by `core.pipeline.config.get_scorer()`.
