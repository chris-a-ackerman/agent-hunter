import json
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


# -- A2A (AGE-57, HW8 wire contracts §3) --------------------------------------------------------

FIXTURE_DOC = "1070b4b25d3c"  # pinned by Nanda Town's a2a-invoice-extraction profile (AGE-58)


def _send(client, slug: str, body, *, slash: bool = True, method: str = "message/send"):
    """One JSON-RPC message/send whose single text part is `body` (JSON-encoded unless a str)."""
    text = body if isinstance(body, str) else json.dumps(body)
    envelope = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": method,
        "params": {
            "message": {
                "role": "user",
                "messageId": "m-1",
                "parts": [{"kind": "text", "text": text}],
            }
        },
    }
    r = client.post(f"/p/{slug}/a2a" + ("/" if slash else ""), json=envelope)
    assert r.status_code == 200
    return r.json()


def _a2a_body(doc_id: str, request_id: str = "order-1", **changes) -> dict:
    return {**_task(doc_id), "request_id": request_id, "nonce": "n-1", **changes}


def _reply_text(task: dict) -> str:
    [artifact] = task["artifacts"]
    [part] = artifact["parts"]
    assert part["kind"] == "text"
    return part["text"]


def _error_code(task: dict) -> str:
    [part] = task["status"]["message"]["parts"]
    return json.loads(part["text"])["error"]


@pytest.mark.parametrize("slug", list(MOCKS))
def test_a2a_card_is_a_standard_card_with_our_fields(truth, slug):
    client = TestClient(create_app(truth_dir=truth[0], base_url="http://127.0.0.1:8101"))
    a2a = client.get(f"/p/{slug}/a2a/.well-known/agent-card.json").json()
    for key in (
        "name",
        "version",
        "url",
        "skills",
        "defaultInputModes",
    ):  # Town's probe needs these
        assert a2a[key]
    assert a2a["url"] == f"http://127.0.0.1:8101/p/{slug}/a2a/"
    assert a2a["skills"][0]["id"] == "invoice_extraction"
    assert a2a["defaultInputModes"] == a2a["defaultOutputModes"] == ["text"]
    plain = AgentCard.model_validate(client.get(f"/p/{slug}/.well-known/agent-card.json").json())
    assert (a2a["name"], a2a["version"], a2a["description"]) == (
        plain.name,
        plain.version,
        plain.description,
    )
    assert {
        k: a2a[k] for k in ("slug", "job_types", "price_per_doc_usd", "region")
    } == plain.model_dump(include={"slug", "job_types", "price_per_doc_usd", "region"})


def test_a2a_card_url_defaults_to_the_mock_base_url(truth, monkeypatch):
    monkeypatch.setenv("MOCK_PROVIDERS_BASE_URL", "http://mocks.test:9000/")
    client = TestClient(create_app(truth_dir=truth[0]))
    card = client.get("/p/mock-a/a2a/.well-known/agent-card.json").json()
    assert card["url"] == "http://mocks.test:9000/p/mock-a/a2a/"


@pytest.mark.parametrize("slash", [True, False])
def test_a2a_happy_path_returns_one_canonical_result(client, truth, slash):
    doc = truth[1][0]
    task = _send(client, "mock-a", _a2a_body(doc.doc_id), slash=slash)["result"]
    assert task["kind"] == "task" and task["status"]["state"] == "completed"
    text = _reply_text(task)
    reply = json.loads(text)
    assert text == json.dumps(reply, sort_keys=True, separators=(",", ":"))
    assert reply.pop("request_id") == "order-1"
    result = ProviderResult.model_validate(reply)
    assert result.task_id == f"t-{doc.doc_id}" and result.cost_usd == MOCKS["mock-a"].cost_usd
    assert result.output == mock_output("mock-a", doc.doc_id, doc.truth)


def test_a2a_identical_retry_returns_the_stored_reply_without_new_work(client, truth, monkeypatch):
    import mocks.fake_providers.app as app_module

    calls = []
    real = app_module.mock_output
    monkeypatch.setattr(app_module, "mock_output", lambda *a: calls.append(a) or real(*a))
    body = _a2a_body(truth[1][0].doc_id)
    first = _send(client, "mock-b", body)["result"]
    again = _send(client, "mock-b", body)["result"]
    assert _reply_text(again) == _reply_text(first)
    assert len(calls) == 1
    other = _send(client, "mock-b", _a2a_body(truth[1][0].doc_id, request_id="order-2"))["result"]
    assert other["status"]["state"] == "completed" and len(calls) == 2


def test_a2a_reused_request_id_with_changed_terms_is_rejected(client, truth):
    doc_id = truth[1][0].doc_id
    _send(client, "mock-a", _a2a_body(doc_id))
    task = _send(client, "mock-a", _a2a_body(doc_id, offered_price=0.5))["result"]
    assert task["status"]["state"] == "rejected"
    assert _error_code(task) == "conflicting_terms"
    assert task["artifacts"] == []


@pytest.mark.parametrize(
    "body",
    [
        {
            "request_id": "probe-1",
            "sku": "widget",
            "quantity": 2,
            "unit_price_cents": 1995,
        },  # Town's probe
        "not json",
        ["a", "list"],
    ],
)
def test_a2a_body_that_is_not_a_provider_task_fails(client, body):
    task = _send(client, "mock-a", body)["result"]
    assert task["status"]["state"] == "failed"
    assert _error_code(task) == "invalid_request"


def test_a2a_protocol_errors_are_json_rpc_errors(client, truth):
    assert (
        _send(client, "mock-a", _a2a_body(truth[1][0].doc_id), method="tasks/get")["error"]["code"]
        == -32601
    )
    r = client.post("/p/mock-a/a2a/", content=b"{not json")
    assert r.json()["error"]["code"] == -32700
    no_text = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {"message": {"parts": []}},
    }
    assert client.post("/p/mock-a/a2a/", json=no_text).json()["error"]["code"] == -32602
    assert client.post("/p/nobody/a2a/", json=no_text).status_code == 404


def test_a2a_timeout_mock_stays_slow(client, truth):
    start = time.monotonic()
    task = _send(client, "mock-d", {**_a2a_body(truth[1][0].doc_id), "deadline_ms": 50})["result"]
    assert time.monotonic() - start >= 0.1
    assert task["status"]["state"] == "failed"
    assert _error_code(task) == "deadline_exceeded"


def test_a2a_invalid_mock_completes_with_a_non_invoice(client, truth):
    task = _send(client, "mock-e", _a2a_body(truth[1][0].doc_id))["result"]
    assert task["status"]["state"] == "completed"
    with pytest.raises(ValidationError):
        Invoice.model_validate(json.loads(_reply_text(task))["output"])


def test_a2a_failures_carry_the_error_code(truth):
    budget = TestClient(create_app(truth_dir=truth[0], fail_budget=True))
    assert (
        _error_code(_send(budget, "mock-a", _a2a_body(truth[1][0].doc_id))["result"])
        == "budget_exhausted"
    )
    client = TestClient(create_app(truth_dir=truth[0]))
    unknown = _send(client, "mock-a", _a2a_body("0" * 12))["result"]
    assert unknown["status"]["state"] == "failed" and _error_code(unknown) == "unknown_document"
    bad_url = _send(client, "mock-a", _a2a_body("0" * 12, document_url="http://x/doc.pdf"))[
        "result"
    ]
    assert _error_code(bad_url) == "bad_document_url"


def test_fixture_answer_key_is_a_fallback_for_both_transports(tmp_path):
    from mocks.fake_providers.app import FIXTURES_DIR

    fixture = Invoice.model_validate_json((FIXTURES_DIR / f"{FIXTURE_DOC}.json").read_text())
    client = TestClient(create_app(truth_dir=tmp_path))  # empty TRUTH_DIR
    raw = "https://raw.githubusercontent.com/chris-a-ackerman/nandatown/agent-hunter-fixture-v1"
    url = f"{raw}/examples/agent-hunter/fixtures/d/{FIXTURE_DOC}.pdf"
    http = client.post("/p/mock-a/tasks", json={**_task(FIXTURE_DOC), "document_url": url})
    assert http.status_code == 200
    assert http.json()["output"] == mock_output("mock-a", FIXTURE_DOC, fixture)
    task = _send(client, "mock-a", _a2a_body(FIXTURE_DOC, document_url=url))["result"]
    assert json.loads(_reply_text(task))["output"] == http.json()["output"]
