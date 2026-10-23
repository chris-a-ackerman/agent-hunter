import httpx
import pytest
from fastapi.testclient import TestClient

from core.docserver import create_app, serve_in_thread

DOC_ID = "0123456789ab"


@pytest.fixture
def data(tmp_path):
    """data/docs/{id}.pdf and data/truth/{id}.json side by side, as in D17."""
    docs, truth = tmp_path / "docs", tmp_path / "truth"
    docs.mkdir()
    truth.mkdir()
    (docs / f"{DOC_ID}.pdf").write_bytes(b"%PDF-1.7 doc")
    (truth / f"{DOC_ID}.json").write_text('{"secret": "answer"}')
    (truth / "fedcba987654.pdf").write_bytes(b"%PDF-1.7 not a doc")
    return docs


@pytest.fixture
def client(data):
    return TestClient(create_app(data))


def test_serves_an_existing_pdf(client):
    resp = client.get(f"/d/{DOC_ID}.pdf")
    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.7 doc"
    assert resp.headers["content-type"] == "application/pdf"


@pytest.mark.parametrize(
    "path",
    [
        "/d/ffffffffffff.pdf",  # well-formed but missing
        "/d/0123456789AB.pdf",  # upper case
        "/d/0123456789a.pdf",  # 11 chars
        "/d/0123456789abc.pdf",  # 13 chars
        "/d/not-a-hex-id.pdf",
        f"/d/{DOC_ID}.json",
        "/d/..%2Ftruth%2F0123456789ab.pdf",
        "/d/..%2Ftruth%2F0123456789ab.json",
        "/d/../truth/0123456789ab.json",
        "/d/%2E%2E%2Ftruth%2Ffedcba987654.pdf",
        "/d/fedcba987654.pdf",  # exists only under truth/
        "/truth/0123456789ab.json",
    ],
)
def test_rejects_anything_but_a_hex_doc_id_in_docs_dir(client, path):
    resp = client.get(path)
    assert resp.status_code == 404
    assert b"answer" not in resp.content and b"not a doc" not in resp.content


def test_serve_in_thread(data, unused_port):
    server = serve_in_thread(port=unused_port, root=data)
    try:
        resp = httpx.get(f"http://127.0.0.1:{unused_port}/d/{DOC_ID}.pdf")
        assert resp.status_code == 200 and resp.content == b"%PDF-1.7 doc"
    finally:
        server.should_exit = True


@pytest.fixture
def unused_port():
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
