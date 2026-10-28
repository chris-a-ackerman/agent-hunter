"""The demo web app (AGE-32): the page is served, and a run's view reports every step, the
provider lanes, scores, verdict and costs. Stub steps and in-process mock providers (no model
calls); `make_steps` and `store` come from tests/test_pipeline/conftest.py."""

import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from core.pipeline import stubs
from core.web import EXAMPLES, create_app
from tests.test_pipeline.conftest import make_steps, store  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    """Every key a run sets, so monkeypatch restores it afterwards."""
    slugs = "mock-a,mock-b,mock-c,mock-d,mock-e"
    for k, v in {"SCORER": "mock", "PROVIDER_SLUGS": slugs, "SAMPLES_DIR": "unused"}.items():
        monkeypatch.setenv(k, v)


def client(store, steps) -> TestClient:  # noqa: F811
    return TestClient(create_app(store, build_steps=lambda *_: steps, serve_docs=False))


def wait(c: TestClient, job_id: str, timeout_s: float = 20) -> dict:
    deadline = time.monotonic() + timeout_s
    while True:
        view = c.get(f"/api/runs/{job_id}").json()
        if not view["running"]:
            return view
        assert time.monotonic() < deadline, "run didn't finish"
        time.sleep(0.05)


def start(c: TestClient, example: str, **kw) -> str:
    resp = c.post("/api/runs", json={"example": example} | kw)
    assert resp.status_code == 202, resp.text
    return resp.json()["job_id"]


def test_index_and_config(store, make_steps):  # noqa: F811
    c = client(store, make_steps())
    page = c.get("/")
    assert page.status_code == 200 and "Trial-then-Hire" in page.text
    cfg = c.get("/api/config").json()
    assert [e["key"] for e in cfg["examples"]] == [e.key for e in EXAMPLES]
    clean = cfg["examples"][0]
    assert clean["samples"] == 3 and clean["n_real_docs"] == 15 and clean["region"] == "any"
    assert cfg["defaults"] == {"mock": False, "scorer": "mock"}


def test_run_reports_every_step_lanes_scores_and_verdict(store, make_steps):  # noqa: F811
    c = client(store, make_steps())
    job_id = start(c, "inv-clean", scorer="mock")
    view = wait(c, job_id)

    assert view["outcome"] == "ranked" and view["error"] is None
    assert [s["state"] for s in view["steps"]] == ["done"] * 8
    assert all(s["duration_s"] is not None for s in view["steps"])
    assert view["review"]["decision"] == "approve"
    assert len(view["rubric"]["weights"]) == 6
    docs = view["trial"]["docs"]
    assert {d["source"] for d in docs} == {"sample"} and all(d["vendor"] for d in docs)
    assert view["eval"] == {"answer_keys": len(docs)}

    cands = view["candidates"]
    assert cands["done"] and cands["unavailable"] == []
    assert all(p["qualifies"] for p in cands["providers"])
    lanes = view["lanes"]
    assert lanes["docs"] == [d["doc_id"] for d in docs]
    assert len(lanes["providers"]) == 5
    for lane in lanes["providers"]:
        assert all(cell is not None for cell in lane["cells"])
        assert sum(lane["counts"].values()) == len(docs)
        assert lane["finished_s"] is not None
    # mock-d sleeps past the deadline
    (slow,) = [lane for lane in lanes["providers"] if lane["slug"] == "mock-d"]
    assert slow["counts"] == {"timeout": len(docs)}

    assert len(view["scores"]) == 5
    assert view["verdict"]["ranking"]
    record = store.get_job(job_id)
    assert view["costs"]["providers_usd"] == pytest.approx(
        sum(s.cost_usd or 0 for s in record.submissions)
    )
    assert view["costs"]["total_usd"] == pytest.approx(
        view["costs"]["platform_usd"] + view["costs"]["providers_usd"]
    )


def test_rejected_job_stops_after_the_review(store, make_steps):  # noqa: F811
    c = client(store, make_steps())
    view = wait(c, start(c, "cover"))
    assert view["outcome"] == "rejected"
    assert [s["state"] for s in view["steps"]] == ["failed"] + ["skipped"] * 7
    assert view["review"]["decision"] == "reject" and view["review"]["reason"]
    assert view["lanes"] is None and view["verdict"] is None
    assert view["costs"]["providers_usd"] == 0


def test_a_provider_whose_card_is_down_is_left_out(store, make_steps, monkeypatch):  # noqa: F811
    monkeypatch.setenv("PROVIDER_SLUGS", "mock-a,mock-b,mock-c,mock-d,mock-e,mock-gone")
    c = client(store, make_steps())
    view = wait(c, start(c, "inv-clean"))
    assert view["candidates"]["unavailable"] == ["mock-gone"]
    assert "mock-gone" not in {lane["slug"] for lane in view["lanes"]["providers"]}


def test_failing_step_is_reported_and_the_rest_skipped(store, make_steps):  # noqa: F811
    def broken(*_):
        raise RuntimeError("rubric exploded")

    c = client(store, make_steps(rubric=broken))
    view = wait(c, start(c, "inv-clean"))
    assert view["outcome"] == "error" and "rubric exploded" in view["error"]
    states = [s["state"] for s in view["steps"]]
    assert states == ["done", "failed"] + ["skipped"] * 6


def test_one_run_at_a_time_and_the_running_view(store, make_steps):  # noqa: F811
    release = threading.Event()

    def slow_review(posting):
        assert release.wait(10)
        return stubs.review_job_stub(posting)

    c = client(store, make_steps(review=slow_review))
    job_id = start(c, "inv-clean")
    view = c.get(f"/api/runs/{job_id}").json()
    assert view["running"] and view["outcome"] is None
    assert view["steps"][0]["state"] in ("pending", "running")
    assert all(s["state"] == "pending" for s in view["steps"][1:])
    assert c.post("/api/runs", json={"example": "inv-long"}).status_code == 409
    release.set()
    assert wait(c, job_id)["outcome"] == "ranked"
    assert c.post("/api/runs", json={"example": "inv-long"}).status_code == 202


def test_each_run_sets_the_scorer_and_restores_the_environment(store, make_steps):  # noqa: F811
    c = client(store, make_steps())
    wait(c, start(c, "cover", scorer="real"))
    assert os.environ["SCORER"] == "real"
    wait(c, start(c, "cover", scorer="mock"))
    assert os.environ["SCORER"] == "mock"


def test_unknown_example_and_run(store, make_steps):  # noqa: F811
    c = client(store, make_steps())
    assert c.post("/api/runs", json={"example": "nope"}).status_code == 404
    assert c.get("/api/runs/job-nope").status_code == 404
