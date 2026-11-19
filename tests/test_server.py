import threading

import pytest
from fastapi.testclient import TestClient

from saburo import agent, config, security, server

PASSWORD = "a-very-long-test-password"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(config, "PASSWORD", PASSWORD)
    monkeypatch.setattr(server, "limiter", security.LoginLimiter())
    monkeypatch.setattr(server.time, "sleep", lambda s: None)
    return TestClient(server.app)


def login(c, password=PASSWORD):
    return c.post("/api/login", json={"password": password})


def test_api_docs_are_not_exposed(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_login_lockout(client):
    for _ in range(security.PER_CLIENT_LIMIT):
        assert login(client, "wrong").status_code == 401
    r = login(client)  # 正しいパスワードでも締め出し中は入れない
    assert r.status_code == 429 and "分間" in r.json()["detail"]


def test_requires_login_and_security_headers(client):
    r = client.get("/api/conversations")
    assert r.status_code == 401
    assert r.headers["x-frame-options"] == "DENY"
    assert login(client).status_code == 200
    assert client.get("/api/conversations").status_code == 200


def test_chat_runs_in_background_and_can_be_polled(client, monkeypatch):
    gate = threading.Event()

    def fake_chat(conversation_id, text):
        yield {"type": "status", "text": "考え中…"}
        gate.wait(5)
        yield {"type": "text", "text": f"答え: {text}"}
        yield {"type": "done"}

    monkeypatch.setattr(agent, "chat", fake_chat)
    login(client)
    r = client.post("/api/chat", json={"message": "こんにちは"}).json()
    job_id, cid = r["job_id"], r["conversation_id"]

    # 作成中は同じ会話に送れない。開き直すと作成中の job が分かる
    assert client.post("/api/chat", json={"message": "次", "conversation_id": cid}).status_code == 409
    assert client.get(f"/api/conversations/{cid}/job").json()["job_id"] == job_id

    first = client.get(f"/api/jobs/{job_id}?after=0").json()
    assert first["events"][0]["type"] == "status" and not first["done"]
    gate.set()
    events, after = first["events"], first["next"]
    while True:
        data = client.get(f"/api/jobs/{job_id}?after={after}").json()
        events += data["events"]
        after = data["next"]
        if data["done"]:
            break
    assert any(e.get("text") == "答え: こんにちは" for e in events)
    assert client.get(f"/api/conversations/{cid}/job").json()["job_id"] is None
    assert client.get("/api/jobs/unknown").status_code == 404


def test_logout_all_invalidates_existing_cookie(client):
    login(client)
    cookie = client.cookies.get(server.COOKIE)
    assert client.post("/api/logout-all").status_code == 200
    client.cookies.set(server.COOKIE, cookie)
    assert client.get("/api/conversations").status_code == 401
