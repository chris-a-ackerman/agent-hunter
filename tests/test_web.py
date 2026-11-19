"""The demo web app (AGE-32, AGE-67): the page is served, and a run's view reports every step, the
candidates, provider lanes, scores, verdict, costs, mode and tie-break. Stub steps and in-process
mock providers (no model calls); `make_steps` and `store` come from tests/test_pipeline/conftest.py.
Offline mode uses real doc, mock-provider and A2A agent servers on free localhost ports."""

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.pipeline import stubs
from core.store import JsonStore
from core.web import EXAMPLES, INDEX_HTML, create_app, timeline_dir
from tests.test_a2a_offline import free_port, model_calls  # noqa: F401  (fixture)
from tests.test_pipeline.conftest import make_steps, store  # noqa: F401  (fixtures)
from tests.test_registry import TOWN_CARD


@pytest.fixture(autouse=True)
def env(monkeypatch):
    """Every key a run sets, so monkeypatch restores it afterwards; nothing from the shell's
    environment that would change a run."""
    slugs = "mock-a,mock-b,mock-c,mock-d,mock-e"
    for k, v in {"SCORER": "mock", "PROVIDER_SLUGS": slugs, "SAMPLES_DIR": "unused"}.items():
        monkeypatch.setenv(k, v)
    for k in ("PROVIDER_A2A_URLS", "HW7_CHANGE", "AGENT_HUNTER_OFFLINE", "PROVIDER_TRANSPORT"):
        monkeypatch.delenv(k, raising=False)


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

    assert view["outcome"] == "ranked" and view["error"] is None, view["error"]
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
    resp = c.post("/api/runs", json={"example": "inv-long"})
    assert resp.status_code == 202
    # Let it finish: a daemon run still going at interpreter exit aborts pytest (exit 134).
    wait(c, resp.json()["job_id"])


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


# --- tie_break and mode (AGE-67) --------------------------------------------------------------


@pytest.mark.parametrize(
    "change, expected", [(None, "accuracy_first"), ("on", "accuracy_first"), ("off", "cost")]
)
def test_config_and_run_view_report_the_tie_break(store, make_steps, monkeypatch, change, expected):  # noqa: F811
    if change is not None:
        monkeypatch.setenv("HW7_CHANGE", change)
    c = client(store, make_steps())
    cfg = c.get("/api/config").json()
    assert cfg["tie_break"] == expected and cfg["mode"] == "live"
    view = wait(c, start(c, "cover"))
    assert view["tie_break"] == expected and view["mode"] == "live"


def test_live_candidates_report_their_listing_source(store, make_steps):  # noqa: F811
    c = client(store, make_steps())
    cands = wait(c, start(c, "inv-clean"))["candidates"]
    assert {p["source"] for p in cands["providers"]} == {"slugs"}
    assert {p["transport"] for p in cands["providers"]} == {"http"}
    assert all(p["reason"] is None for p in cands["providers"])
    assert cands["unlisted"] == []


# --- offline mode (AGE-67): the A2A door's offline wiring, real servers on free ports ----------


@pytest.fixture
def offline(monkeypatch, tmp_path, model_calls):  # noqa: F811
    """`create_app(offline=True)` with no `.env` and no Parley key; everything on localhost."""
    for key in ("PARLEY_API_KEY", "PARLEY_ACCOUNT", "PARLEY_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    env = {
        "DOCS_BASE_URL": f"http://127.0.0.1:{free_port()}",
        "MOCK_PROVIDERS_BASE_URL": f"http://127.0.0.1:{free_port()}",
        "DOCS_DIR": str(tmp_path / "docs"),
        "TRUTH_DIR": str(tmp_path / "truth"),
        "STORE_DIR": str(tmp_path / "store"),
        "MOCK_TASK_DEADLINE_MS": "300",  # mock-d sleeps 2 × deadline: it must time out
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return TestClient(create_app(offline=True))


def test_offline_config_says_so_and_fixes_the_toggles(offline):
    cfg = offline.get("/api/config").json()
    assert cfg["mode"] == "offline" and cfg["tie_break"] == "accuracy_first"
    assert cfg["defaults"] == {"mock": True, "scorer": "real"}


def test_offline_runs_every_invoice_example_and_rejects_the_cover_letter(
    offline,
    model_calls,  # noqa: F811
    tmp_path,
):
    mocks = {"mock-a@1", "mock-b@1", "mock-c@1", "mock-d@1", "mock-e@1"}
    for example in ("inv-clean", "inv-long", "inv-mixed"):
        # The rehearsal toggles don't apply offline: the mocks and the real Scorer, always.
        view = wait(offline, start(offline, example, mock=False, scorer="mock"))
        assert view["outcome"] == "ranked", (example, view["error"])
        assert view["mode"] == "offline" and view["mock"] and view["scorer"] == "real"
        assert [s["state"] for s in view["steps"]] == ["done"] * 8
        assert {r["provider"] for r in view["verdict"]["ranking"]} == mocks
        assert view["costs"]["platform_usd"] == 0 and view["costs"]["providers_usd"] > 0
        assert view["candidates"]["unavailable"] == [] and view["candidates"]["unlisted"] == []
    view = wait(offline, start(offline, "cover"))
    assert view["outcome"] == "rejected" and view["mode"] == "offline"
    assert view["costs"]["providers_usd"] == 0
    assert model_calls == []
    assert list((tmp_path / "store").rglob("*.json"))  # STORE_DIR, not .data/store


def test_offline_uses_its_own_store_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("STORE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    c = TestClient(create_app(offline=True, serve_docs=False))
    assert c.get("/api/config").json()["mode"] == "offline"
    assert not (tmp_path / ".data" / "store").exists()


# --- A2A-listed agents in the candidates panel (AGE-59/60) -------------------------------------


def serve(app, name: str) -> tuple[str, object]:
    from core.docserver import serve_app_in_thread

    port = free_port()
    return f"http://127.0.0.1:{port}", serve_app_in_thread(app, port=port, name=name)


@pytest.fixture
def quote_agent():
    """An outside A2A agent with a plain card that sells quotes (Town's reference card)."""
    from fastapi import FastAPI

    app = FastAPI()
    app.get("/.well-known/agent-card.json")(lambda: TOWN_CARD)

    @app.post("/")
    def reply(body: dict) -> dict:
        return {"jsonrpc": "2.0", "id": body.get("id"), "result": "quote"}

    url, server = serve(app, "quote agent")
    yield url
    server.should_exit = True


@pytest.fixture
def misleading_agent(quote_agent):
    """The quote agent behind `mocks.misleading_card`: its card claims invoice extraction."""
    from core.docserver import serve_app_in_thread
    from mocks.misleading_card import create_app as create_misleading

    port = free_port()
    url = f"http://127.0.0.1:{port}"
    app = create_misleading(quote_agent, public_url=url)
    server = serve_app_in_thread(app, port=port, name="misleading card")
    yield url
    server.should_exit = True


def test_offline_candidates_include_the_misleading_a2a_agent(
    offline,
    misleading_agent,
    monkeypatch,
    model_calls,  # noqa: F811
):
    dead = f"http://127.0.0.1:{free_port()}"  # nothing listens here
    monkeypatch.setenv("PROVIDER_A2A_URLS", f"{misleading_agent},{dead}")
    view = wait(offline, start(offline, "inv-clean"))
    assert view["outcome"] == "ranked", view["error"]
    cands = view["candidates"]
    rows = {p["id"]: p for p in cands["providers"]}
    agent = rows["nandatown-quote-seller@0.3.1"]
    # Its card claims invoice extraction at a list price, so the Candidate Finder keeps it ...
    assert agent["source"] == "a2a" and agent["transport"] == "a2a"
    assert agent["url"] == misleading_agent and agent["qualifies"] and agent["reason"] is None
    assert {p["source"] for p in rows.values() if p["id"] != agent["id"]} == {"slugs"}
    assert cands["unlisted"] == [
        {
            "id": f"{dead}/.well-known/agent-card.json",
            "reason": "card fetch failed (ConnectError)",
            "source": "a2a",
        }
    ]
    # ... and the trial shows it can't do the job.
    (lane,) = [lane for lane in view["lanes"]["providers"] if lane["id"] == agent["id"]]
    assert "ok" not in lane["counts"] and sum(lane["counts"].values()) == len(lane["cells"])
    (row,) = [r for r in view["verdict"]["ranking"] if r["provider"] == agent["id"]]
    assert row["accuracy"] == 0 and view["verdict"]["recommended"] != agent["id"]
    assert model_calls == []


def test_offline_candidates_give_the_reason_an_a2a_agent_was_excluded(
    offline, quote_agent, monkeypatch
):
    monkeypatch.setenv("PROVIDER_A2A_URLS", quote_agent)
    view = wait(offline, start(offline, "inv-clean"))
    (agent,) = [p for p in view["candidates"]["providers"] if p["source"] == "a2a"]
    assert agent["qualifies"] is False
    assert agent["reason"] == "doesn't declare job type 'invoice_extraction' (declares ['quote'])"
    assert agent["id"] not in {lane["id"] for lane in view["lanes"]["providers"]}


# --- the page's script (no browser here: node, if it's installed) ------------------------------

# Just enough DOM for the page's script to load and render to a string; then checks on the
# rendered HTML: the ?demo fixture plays to the end, the tie wording, the offline badge, the
# candidates panel and the unlisted A2A card banner.
PAGE_PRELUDE = """
const noop = () => {};
const el = () => ({ childNodes: [], addEventListener: noop, querySelector: () => null,
  setAttribute: noop, insertBefore: noop, removeChild: noop });
globalThis.document = { getElementById: el, documentElement: el(),
  createElement: () => ({ innerHTML: '', content: { childNodes: [] } }) };
globalThis.location = { search: '?demo', protocol: 'http:' };
globalThis.localStorage = { getItem: () => null, setItem: noop };
globalThis.matchMedia = () => ({ matches: false });
globalThis.window = { scrollTo: noop };
"""

PAGE_CHECKS = """
const fails = [];
const check = (ok, what) => { if (!ok) fails.push(what); };
let html = '';
const has = (text) => html.includes(text);
let end = null;
for (let t = 0; t <= 60; t += 0.25) {  // every moment of the fixture renders
  S.view = fixtureView('inv-clean', t); renderApp(); end = S.view;
}
check(!end.running && end.outcome === 'ranked', 'fixture plays to the end');
check(end.verdict.status === 'tie', 'fixture ends in a tie');
check(end.tie_break === 'accuracy_first' && end.mode === 'live', 'fixture view shape');
html = renderApp();
check(has('tie broken on trial accuracy') && !has('tie broken on cost'), 'accuracy wording');
check(has('Fixture playback') && !has('Offline · stub agents'), 'fixture chip only');
check(has('outside-agent') && has('>A2A</span>'), 'A2A row');
check(has('outside-agent@0.1.0</span> (A2A): doesn&#39;t declare job type'), 'exclusion reason');
check(has('7 of 8 qualify'), 'candidates summary');
S.view = { ...end, tie_break: 'cost' };
html = renderApp();
check(has('tie broken on cost') && !has('tie broken on trial accuracy'), 'cost wording');
S.mode = 'live'; S.serverMode = 'offline';
const dead = { id: 'http://127.0.0.1:9/.well-known/agent-card.json',
  reason: 'card fetch failed (ConnectError)', source: 'a2a' };
S.view = { ...end, mode: 'offline', candidates: { ...end.candidates, unlisted: [dead] } };
html = renderApp();
check(has('Offline · stub agents') && !has('chip-mock'), 'offline badge');
check(has('offline stub Ranker'), 'offline rationale label');
check(has('left out: card fetch failed (ConnectError)'), 'unlisted banner');
S.mode = 'fixture'; S.serverMode = null; S.selected = 'cover';
for (let t = 0; t <= 3; t += 0.25) { S.view = fixtureView('cover', t); html = renderApp(); }
check(S.view.outcome === 'rejected' && has('Rejected by the Job Reviewer'), 'cover rejected');
if (fails.length) { console.error('FAILED: ' + fails.join('; ')); process.exit(1); }
console.log('page ok');
"""


def page_script() -> str:
    (script,) = re.findall(r"<script>(.*?)</script>", INDEX_HTML.read_text("utf-8"), re.S)
    return script


@pytest.mark.skipif(shutil.which("node") is None, reason="node isn't installed")
def test_page_script_parses_and_renders_the_fixture(tmp_path: Path):
    node = shutil.which("node")
    assert node is not None
    script = tmp_path / "page.js"
    script.write_text(page_script(), "utf-8")
    check = subprocess.run([node, "--check", str(script)], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    harness = tmp_path / "harness.js"
    harness.write_text(PAGE_PRELUDE + page_script() + PAGE_CHECKS, "utf-8")
    run = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0 and "page ok" in run.stdout, run.stderr


# --- timelines and replays (AGE-68) ------------------------------------------------------------


def timeline(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def test_a_run_records_its_timeline_in_the_store(store, make_steps):  # noqa: F811
    c = client(store, make_steps())
    job_id = start(c, "inv-clean")
    final = wait(c, job_id)
    snaps = timeline(store.root / "web_runs" / f"{job_id}.jsonl")
    assert all(set(s) == {"t", "view"} for s in snaps)
    ts = [s["t"] for s in snaps]
    assert ts == sorted(ts)
    assert snaps[-1]["view"]["running"] is False and snaps[-1]["view"]["outcome"] == "ranked"
    assert snaps[-1]["view"]["steps"] == final["steps"]
    assert snaps[0]["view"]["started_at"] == final["started_at"]
    assert final["started_at"] == store.get_job(job_id).created_at.isoformat()


def test_a_failing_timeline_write_does_not_fail_the_run(store, make_steps, caplog):  # noqa: F811
    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / "web_runs").write_text("not a directory")  # every write fails
    c = client(store, make_steps())
    with caplog.at_level("WARNING", logger="core.web"):
        view = wait(c, start(c, "inv-clean"))
    assert view["outcome"] == "ranked" and view["error"] is None
    assert "timeline write failed" in caplog.text
    assert c.get("/api/replays").json() == []


def test_timeline_dir_resolves_store_dir_like_the_json_store(monkeypatch, tmp_path):
    monkeypatch.delenv("STORE_DIR", raising=False)
    assert timeline_dir() == Path(".data/store/web_runs")
    assert timeline_dir(offline=True) == Path(".data/offline-store/web_runs")
    monkeypatch.setenv("STORE_DIR", str(tmp_path))
    assert timeline_dir(offline=True) == tmp_path / "web_runs"
    assert timeline_dir(JsonStore(tmp_path / "other")) == tmp_path / "other" / "web_runs"


def test_offline_inv_mixed_timeline_replays_and_exports(offline, tmp_path, monkeypatch):
    job_id = start(offline, "inv-mixed")
    final = wait(offline, job_id)
    assert final["outcome"] == "ranked", final["error"]
    path = tmp_path / "store" / "web_runs" / f"{job_id}.jsonl"  # STORE_DIR, offline
    snaps = timeline(path)

    # A snapshot after every step start and finish, every task_done, and a final one.
    steps = [s["key"] for s in final["steps"]]
    for key in steps:
        states = [next(x["state"] for x in s["view"]["steps"] if x["key"] == key) for s in snaps]
        assert "running" in states and "done" in states, key
    filled = [
        sum(c is not None for lane in s["view"]["lanes"]["providers"] for c in lane["cells"])
        for s in snaps
        if s["view"]["lanes"]
    ]
    tasks = len(final["lanes"]["docs"]) * len(final["lanes"]["providers"])
    assert set(range(1, tasks + 1)) <= set(filled)
    assert len(snaps) >= 2 * len(steps) + tasks + 1
    assert snaps[-1]["view"]["running"] is False and snaps[-2]["view"]["running"] is True
    assert snaps[-1]["t"] == pytest.approx(final["elapsed_s"], abs=0.01)

    # The API: listed, returned oldest first, 404 for anything else.
    (entry,) = offline.get("/api/replays").json()
    assert entry == {
        "job_id": job_id,
        "example": "inv-mixed",
        "mode": "offline",
        "created_at": final["started_at"],
        "duration_s": snaps[-1]["t"],
        "outcome": "ranked",
    }
    assert offline.get(f"/api/replays/{job_id}").json() == snaps
    assert offline.get("/api/replays/job-nope").status_code == 404
    assert offline.get("/api/replays/..%2Fjobs").status_code == 404

    # --export-replay writes apps/web/replays/<job_id>.json (here a temp dir), served at /replays/.
    import dotenv

    import core.web

    replays = tmp_path / "replays"
    monkeypatch.setattr(core.web, "REPLAYS_DIR", replays)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)  # no .env in tests
    core.web.main(["--offline", "--export-replay", job_id])
    exported = replays / f"{job_id}.json"
    assert json.loads(exported.read_text("utf-8")) == snaps
    assert offline.get(f"/replays/{job_id}.json").json() == snaps
    assert offline.get("/replays/nope.json").status_code == 404
    with pytest.raises(SystemExit):
        core.web.main(["--offline", "--export-replay", "job-nope"])

    # ... and plays in the page: ?replay=replays/<job_id>.json&record (node, if installed).
    if shutil.which("node"):
        run_replay_page(tmp_path, f"replays/{job_id}.json", exported.read_text("utf-8"))


# The replay clock is pure functions of a timeline; checked here with the Oct 28 inv-mixed run's
# shape: 13 s of setup steps, 60 s of trial runner, 7 s of rank.
REPLAY_CLOCK_CHECKS = """
const fails = [];
const check = (ok, what) => { if (!ok) fails.push(what); };
const near = (a, b) => Math.abs(a - b) < 1e-9;
const at = { review: [0, 4], rubric: [4, 4], trial: [8, 5], eval: [13, 0], candidates: [13, 0],
  trial_runner: [13, 60], score: [73, 0], rank: [73, 7] };
const steps = STEPS.map((s) => ({ key: s.key, state: 'done', started_s: at[s.key][0],
  duration_s: at[s.key][1] }));
const tl = [0, 2, 13, 40, 73, 80].map((t) => ({ t, view: { steps, t } }));
const segs = replaySegments(tl, 3, 8);
const total = replayPlayTime(segs, 80);
check(near(total, 13 / 8 + 60 / 3 + 7 / 8) && total <= 25, 'plays in <= 25 s: ' + total);
check(near(replayPlayTime(segs, 13), 1.625) && near(replayPlayTime(segs, 73), 21.625), 'pieces');
check(near(replayRealTime(segs, 1.625), 13) && near(replayRealTime(segs, 11.625), 43), 'inverse');
check(replayRealTime(segs, 1e6) === 80 && replayRealTime(segs, 0) === 0, 'holds at the end');
check(replayFrame(tl, 39.9).t === 13 && replayFrame(tl, 40).t === 40, 'latest frame <= t');
check(replayFrame(tl, -1).t === 0, 'first frame before the first snapshot');
const dflt = replayPlayTime(replaySegments(tl, REPLAY_SPEED, REPLAY_SETUP_SPEED), 80);
check(near(dflt, 22.5), 'defaults 3 and 8: ' + dflt);
check(replaySource('job-abc') === '/api/replays/job-abc', 'job id source');
check(replaySource('replays/job-abc.json') === 'replays/job-abc.json', 'file source');
if (fails.length) { console.error('FAILED: ' + fails.join('; ')); process.exit(1); }
console.log('clock ok');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node isn't installed")
def test_replay_clock_plays_the_inv_mixed_shape_in_25_s(tmp_path: Path):
    node = shutil.which("node")
    assert node is not None
    harness = tmp_path / "clock.js"
    harness.write_text(PAGE_PRELUDE + page_script() + REPLAY_CLOCK_CHECKS, "utf-8")
    run = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0 and "clock ok" in run.stdout, run.stderr


# The page loads a recorded timeline (fetch stubbed with the exported file), then every frame of
# the replay renders with the badge and the run's date; timers show recorded seconds; snapshots
# aren't changed; the recommendation is on screen at the end; space pauses and r restarts.
REPLAY_PRELUDE = """
const listeners = {};
let fetched = null;
document.getElementById = () => ({ ...el(), scrollIntoView: noop });
document.addEventListener = (type, f) => { listeners[type] = f; };
globalThis.setInterval = () => 0;  // the harness moves the replay clock itself
globalThis.fetch = async (url) => {
  fetched = url;
  return { ok: true, json: async () => JSON.parse(RECORDED) };
};
"""

REPLAY_PAGE_CHECKS = """
const fails = [];
const check = (ok, what) => { if (!ok) fails.push(what); };
setTimeout(() => {
  check(S.mode === 'replay' && S.record && fetched === SOURCE, 'loads ' + fetched);
  const R = S.replay;
  check(R && R.timeline.length > 10 && R.date, 'timeline and date');
  check(R.length < R.timeline[R.timeline.length - 1].t, 'sped up');
  const before = JSON.stringify(R.timeline);
  R.playing = false;
  let sawLanes = false, sawTimer = false;
  for (let p = 0; p <= R.length + 0.5; p += 0.02) {
    R.play = Math.min(p, R.length);
    replayTick();
    const html = renderApp();
    const badge = html.includes('Replay of a real run · sped up');
    check(badge && html.includes('run on ' + R.date), 'badge at ' + p);
    check(clock(S.view) >= R.t - 1e-9, 'recorded clock at ' + p);
    const tr = S.view.steps.find((s) => s.key === 'trial_runner');
    if (tr.state === 'running') {
      sawLanes = sawLanes || html.includes('data-k="d-lanes"');
      sawTimer = sawTimer || html.includes(secs(clock(S.view) - tr.started_s));
    }
  }
  check(sawLanes && sawTimer, 'lanes and recorded-seconds timer while providers answer');
  const end = renderApp();
  check(!S.view.running && S.view.outcome === 'ranked', 'ends ranked');
  check(end.includes('Hire <span class="mono">'), 'recommendation on screen at the end');
  check(!end.includes('data-k="d-lanes"'), 'finished steps collapsed');
  check(JSON.stringify(R.timeline) === before, 'snapshots unchanged');
  listeners.keydown({ key: 'r', preventDefault: noop });
  check(R.playing && R.play < 0.05 && S.view.steps[0].state !== 'done', 'r restarts');
  listeners.keydown({ key: ' ', preventDefault: noop });
  check(!R.playing && renderApp().includes('paused'), 'space pauses');
  if (fails.length) { console.error('FAILED: ' + fails.slice(0, 5).join('; ')); process.exit(1); }
  console.log('replay ok');
  process.exit(0);
}, 100);
"""


def run_replay_page(tmp_path: Path, source: str, recorded: str) -> None:
    node = shutil.which("node")
    assert node is not None
    prelude = PAGE_PRELUDE.replace("'?demo'", json.dumps(f"?replay={source}&record"))
    consts = f"const RECORDED = {json.dumps(recorded)};\nconst SOURCE = {json.dumps(source)};\n"
    harness = tmp_path / "replay.js"
    harness.write_text(prelude + consts + REPLAY_PRELUDE + page_script() + REPLAY_PAGE_CHECKS)
    run = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0 and "replay ok" in run.stdout, run.stderr
