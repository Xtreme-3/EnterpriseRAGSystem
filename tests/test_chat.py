"""B6：流式问答 + 溯源测试。

依赖 PostgreSQL + mock provider。
"""
from __future__ import annotations

import io
import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app


def _pg_reachable() -> bool:
    try:
        import psycopg2
        s = Settings()
        conn = psycopg2.connect(
            host=s.pg_host, port=s.pg_port, user=s.pg_user,
            password=s.pg_password, dbname=s.pg_database,
        )
        conn.close()
        return True
    except Exception:
        return False


def _register_and_login(client: TestClient, username: str) -> str:
    client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    return client.post("/api/auth/login", json={"username": username, "password": "secret123"}).json()["access_token"]


def _create_kb(client: TestClient, token: str, name: str) -> int:
    return client.post("/api/kbs", json={"name": name}, headers={"Authorization": f"Bearer {token}"}).json()["id"]


def _upload(client: TestClient, kb_id: int, token: str, filename: str, content: bytes):
    return client.post(
        f"/api/kbs/{kb_id}/documents",
        files={"file": (filename, io.BytesIO(content), "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture(scope="module")
def client() -> TestClient:
    if not _pg_reachable():
        pytest.skip("PostgreSQL 不可达，跳过 B6 问答测试")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    if not _pg_reachable():
        return
    import psycopg2
    s = Settings()
    conn = psycopg2.connect(
        host=s.pg_host, port=s.pg_port, user=s.pg_user,
        password=s.pg_password, dbname=s.pg_database,
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DELETE FROM knowledge_bases WHERE name LIKE 'test_%'")
    cur.execute("DELETE FROM users WHERE username LIKE 'test_%'")
    conn.close()


# ---- B6-1: 非流式问答 ----


def test_ask_returns_answer_and_sources(client: TestClient) -> None:
    token = _register_and_login(client, "test_b6_ask")
    kb_id = _create_kb(client, token, "test_b6_kb")
    _upload(client, kb_id, token, "policy.txt", "出差住宿标准：一线城市每晚不超过六百元。年假满一年享五天。".encode("utf-8"))

    resp = client.post(f"/api/kbs/{kb_id}/ask", json={"query": "出差住宿标准是多少？"},
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "出差住宿标准是多少？"
    assert len(body["answer"]) > 0
    assert len(body["sources"]) >= 1
    source = body["sources"][0]
    assert source["filename"] == "policy.txt"
    assert source["content"]
    assert source["score"] > 0


def test_ask_empty_kb(client: TestClient) -> None:
    token = _register_and_login(client, "test_b6_empty")
    kb_id = _create_kb(client, token, "test_b6_empty_kb")

    resp = client.post(f"/api/kbs/{kb_id}/ask", json={"query": "随便问"},
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert "未找到" in body["answer"]
    assert body["sources"] == []


def test_ask_others_kb_forbidden(client: TestClient) -> None:
    token_alice = _register_and_login(client, "test_b6_cross1")
    token_bob = _register_and_login(client, "test_b6_cross2")
    kb_id = _create_kb(client, token_alice, "test_b6_alice_kb")

    resp = client.post(f"/api/kbs/{kb_id}/ask", json={"query": "test"},
                       headers={"Authorization": f"Bearer {token_bob}"})
    assert resp.status_code == 403


def test_ask_requires_auth(client: TestClient) -> None:
    resp = client.post("/api/kbs/1/ask", json={"query": "test"})
    assert resp.status_code == 401


# ---- B6-2 + B6-3: SSE 流式 + 溯源 ----


def test_ask_stream(client: TestClient) -> None:
    token = _register_and_login(client, "test_b6_stream")
    kb_id = _create_kb(client, token, "test_b6_stream_kb")
    _upload(client, kb_id, token, "doc.txt", "这段内容描述了差旅报销制度：一线城市住宿标准六百元。".encode("utf-8"))

    resp = client.post(f"/api/kbs/{kb_id}/ask/stream", json={"query": "差旅报销"},
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")

    body = resp.text
    lines = body.strip().split("\n\n")

    # 应包含 token 事件、sources 事件、[DONE] 结束标记
    tokens = []
    sources_seen = False
    done_seen = False
    for line in lines:
        if not line.startswith("data: "):
            continue
        data_str = line[6:]  # strip "data: "
        if data_str == "[DONE]":
            done_seen = True
            continue
        payload = json.loads(data_str)
        if payload["type"] == "token":
            tokens.append(payload["content"])
        elif payload["type"] == "sources":
            sources_seen = True
            assert len(payload["sources"]) >= 1
            assert payload["sources"][0]["filename"] == "doc.txt"

    assert len(tokens) > 0
    assert sources_seen
    assert done_seen


# ---- H1：chat 检索模式（mode）----


def test_ask_explicit_mode_vector(client: TestClient) -> None:
    """显式传 mode=vector 走纯向量检索，链路正常。"""
    token = _register_and_login(client, "test_h1_mode_vec")
    kb_id = _create_kb(client, token, "test_h1_mode_vec_kb")
    _upload(client, kb_id, token, "policy.txt", "出差住宿标准：一线城市每晚不超过六百元。".encode("utf-8"))

    resp = client.post(f"/api/kbs/{kb_id}/ask", json={"query": "住宿标准", "mode": "vector"},
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["sources"][0]["filename"] == "policy.txt"


def test_ask_invalid_mode_422(client: TestClient) -> None:
    """非法 mode 值被 pydantic 校验拒绝（422）。"""
    token = _register_and_login(client, "test_h1_mode_bad")
    kb_id = _create_kb(client, token, "test_h1_mode_bad_kb")

    resp = client.post(f"/api/kbs/{kb_id}/ask", json={"query": "test", "mode": "foo"},
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 422
