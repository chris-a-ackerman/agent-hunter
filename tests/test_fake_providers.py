import random
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from contracts import (
    INVOICE_JSON_SCHEMA,
    AgentCard,
    Invoice,
    ProviderError,
    ProviderResult,
    RegisteredProvider,
)
from contracts.rubric import SCORED_FIELDS
from data.synth import LAYOUTS, generate
from mocks.fake_providers import MOCKS, corrupt, create_app, mock_output

CORRUPTING = [s for s, m in MOCKS.items() if m.behaviour == "corrupt"]


@pytest.fixture
def truth(tmp_path):
    """A few answer keys on disk, the way the mock server reads them (D17)."""
    docs = generate(layout="classic", n=3, seed=1, render=False, truth_dir=tmp_path)
    return tmp_path, docs


@pytest.fixture
def client(truth):
    return TestClient(create_app(truth_dir=truth[0]))


def _task(doc_id: str, deadline_ms: int = 60_000) -> dict:
    return {
        "task_id": f"t-{doc_id}",
        "requester_id": "req-test",
        "document_url": f"http://localhost:8200/d/{doc_id}.pdf",
        "output_schema": INVOICE_JSON_SCHEMA,
        "deadline_ms": deadline_ms,
        "offered_price": 0.01,
    }


@pytest.mark.parametrize("slug", list(MOCKS))
def test_agent_card_matches_the_contract(client, slug):
    r = client.get(f"/p/{slug}/.well-known/agent-card.json")
    assert r.status_code == 200
    card = AgentCard.model_validate(r.json())
    assert card.job_types == ["invoice_extraction"] and card.region == "global"
    assert card.price_per_doc_usd == MOCKS[slug].price_per_doc_usd
    reg = RegisteredProvider(card=card, base_url="http://localhost:8101")
    assert reg.provider_id == f"{slug}@1"
    assert reg.tasks_url == f"http://localhost:8101/p/{slug}/tasks"


@pytest.mark.parametrize("slug", CORRUPTING)
def test_corrupting_mock_returns_a_valid_result(client, truth, slug):
    doc = truth[1][0]
    r = client.post(f"/p/{slug}/tasks", json=_task(doc.doc_id))
    assert r.status_code == 200
    result = ProviderResult.model_validate(r.json())
    assert result.task_id == f"t-{doc.doc_id}"
    assert result.cost_usd == MOCKS[slug].cost_usd
    Invoice.model_validate(result.output)  # corrupted values, but still a valid Invoice
    assert result.output == mock_output(slug, doc.doc_id, doc.truth)


def test_answers_are_deterministic(client, truth):
    doc_id = truth[1][1].doc_id
    a = client.post("/p/mock-c/tasks", json=_task(doc_id)).json()
    b = client.post("/p/mock-c/tasks", json=_task(doc_id)).json()
    assert a == b


def test_invalid_mock_returns_ok_but_not_an_invoice(client, truth):
    r = client.post("/p/mock-e/tasks", json=_task(truth[1][0].doc_id))
    assert r.status_code == 200
    result = ProviderResult.model_validate(r.json())  # parses → the Runner records `ok`
    with pytest.raises(ValidationError):
        Invoice.model_validate(result.output)  # → the Scorer records `invalid`


def test_timeout_mock_answers_after_twice_the_deadline(client, truth):
    start = time.monotonic()
    r = client.post("/p/mock-d/tasks", json=_task(truth[1][0].doc_id, deadline_ms=50))
    assert time.monotonic() - start >= 0.1
    assert r.status_code != 200


def test_fail_budget_returns_402(truth):
    client = TestClient(create_app(truth_dir=truth[0], fail_budget=True))
    r = client.post("/p/mock-a/tasks", json=_task(truth[1][0].doc_id))
    assert r.status_code == 402
    assert ProviderError.model_validate(r.json()).error == "budget_exhausted"


def test_errors(client, truth):
    assert client.get("/p/nobody/.well-known/agent-card.json").status_code == 404
    assert client.post("/p/nobody/tasks", json=_task(truth[1][0].doc_id)).status_code == 404
    assert client.post("/p/mock-a/tasks", json=_task("0" * 12)).status_code == 404
    bad_url = {**_task(truth[1][0].doc_id), "document_url": "http://x/doc.pdf"}
    assert client.post("/p/mock-a/tasks", json=bad_url).status_code == 400
    trial_flag = {**_task(truth[1][0].doc_id), "is_trial": True}
    assert client.post("/p/mock-a/tasks", json=trial_flag).status_code == 422


def test_served_slugs_can_be_limited(truth):
    client = TestClient(create_app(slugs=["mock-a"], truth_dir=truth[0]))
    assert client.get("/p/mock-a/.well-known/agent-card.json").status_code == 200
    assert client.get("/p/mock-b/.well-known/agent-card.json").status_code == 404
    with pytest.raises(ValueError):
        create_app(slugs=["mock-z"])


def _clearly_wrong(field: str, got, want) -> bool:
    match field:
        case "total":
            return abs(got - want) > 0.01
        case "line_items":
            return all(
                abs(g["amount"] - w["amount"]) > 0.01 for g, w in zip(got, want, strict=True)
            )
        case "invoice_number":
            return got is not None and got != want
        case _:
            return str(got).lower() != str(want).lower()


def test_corrupted_fields_are_clearly_wrong_and_the_rest_untouched(tmp_path):
    [doc] = generate(layout="compact", n=1, seed=2, render=False, truth_dir=tmp_path)
    truth = doc.truth.model_dump()
    for seed in range(200):
        out, hit = corrupt(doc.truth, 0.5, random.Random(seed))
        for f in SCORED_FIELDS:
            assert _clearly_wrong(f, out[f], truth[f]) == (f in hit), (seed, f)
        assert (out["subtotal"], out["tax"]) == (truth["subtotal"], truth["tax"])


def test_null_invoice_number_gets_a_value(tmp_path):
    inv = generate(layout="classic", n=1, seed=3, render=False, truth_dir=tmp_path)[0].truth
    out, _ = corrupt(inv.model_copy(update={"invoice_number": None}), 0.0, random.Random(0))
    assert out["invoice_number"] is not None


N_KEYS = 5000  # ±2 points is ≥ 2.9 standard deviations per field at this size (p = 0.6)


def test_corruption_rates_match_known_accuracy(tmp_path):
    """AGE-18 acceptance: each mock's per-field corruption rate is within ±2 points of 1 − p."""
    per_layout = N_KEYS // len(LAYOUTS) + 1
    docs = [
        d
        for layout in LAYOUTS
        for d in generate(layout=layout, n=per_layout, seed=18, render=False, truth_dir=tmp_path)
    ][:N_KEYS]
    for slug in CORRUPTING:
        p = MOCKS[slug].p
        counts = dict.fromkeys(SCORED_FIELDS, 0)
        for d in docs:
            _, hit = corrupt(d.truth, p, random.Random(f"{slug}:{d.doc_id}"))
            for f in hit:
                counts[f] += 1
        for f, c in counts.items():
            assert abs(c / len(docs) - (1 - p)) <= 0.02, (slug, f, c / len(docs))
