from fastapi.testclient import TestClient

from contracts import Verdict
from core.api import create_app
from tests.test_pipeline.conftest import load_example


def client(store, steps) -> TestClient:
    return TestClient(create_app(store=store, steps=steps))


def test_post_job_runs_the_pipeline_and_get_returns_the_verdict(store, make_steps):
    c = client(store, make_steps())
    posting = load_example("invoice_clean").model_dump(mode="json")

    resp = c.post("/jobs", json=posting)
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    # TestClient runs background tasks before returning the response.
    body = c.get(f"/jobs/{job_id}").json()
    assert body["status"] == "ranked"
    Verdict.model_validate(body["verdict"])
    assert body["rubric"]["job_id"] == job_id
    assert len(body["scores"]) == 5


def test_get_job_never_returns_the_answer_key(store, make_steps):
    c = client(store, make_steps())
    job_id = c.post("/jobs", json=load_example("invoice_clean").model_dump(mode="json")).json()[
        "job_id"
    ]
    assert store.get_job(job_id).trial is not None
    assert "trial" not in c.get(f"/jobs/{job_id}").json()


def test_rejected_job_is_visible_with_its_reason(store, make_steps):
    c = client(store, make_steps())
    job_id = c.post("/jobs", json=load_example("reject_unscorable").model_dump(mode="json")).json()[
        "job_id"
    ]
    body = c.get(f"/jobs/{job_id}").json()
    assert body["status"] == "rejected"
    assert body["status_history"][-1]["note"] == body["spec"]["reason"]


def test_unknown_job_is_404_and_bad_posting_is_422(store, make_steps):
    c = client(store, make_steps())
    assert c.get("/jobs/job-000000000000").status_code == 404
    assert c.get("/jobs/..%2F..%2Fetc").status_code == 404
    assert c.post("/jobs", json={"requester_id": "r"}).status_code == 422


def test_api_serves_documents(store, make_steps, tmp_path, monkeypatch):
    monkeypatch.setenv("DOCS_DIR", str(tmp_path / "docs"))
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "0123456789ab.pdf").write_bytes(b"%PDF-1.7 test")
    resp = client(store, make_steps()).get("/d/0123456789ab.pdf")
    assert resp.status_code == 200 and resp.content == b"%PDF-1.7 test"
