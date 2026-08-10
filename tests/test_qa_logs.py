"""G4：问答日志埋点 + 历史查询测试。依赖 PostgreSQL + mock provider。"""
from __future__ import annotations

import io

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


def _ask(client: TestClient, kb_id: int, token: str, query: str):
    return client.post(
        f"/api/kbs/{kb_id}/ask",
        json={"query": query},
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.fixture(scope="module")
def client() -> TestClient:
    if not _pg_reachable():
        pytest.skip("PostgreSQL 不可达，跳过 G4 问答日志测试")
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
    cur.execute("DELETE FROM qa_logs WHERE kb_id IN (SELECT id FROM knowledge_bases WHERE name LIKE 'test_g4%')")
    cur.execute("DELETE FROM knowledge_bases WHERE name LIKE 'test_g4%'")
    cur.execute("DELETE FROM users WHERE username LIKE 'test_g4%'")
    conn.close()


# ---- Auth ----

def test_no_token_returns_401(client: TestClient) -> None:
    resp = client.get("/api/kbs/1/qa-logs")
    assert resp.status_code == 401


def test_other_user_kb_returns_403(client: TestClient) -> None:
    token1 = _register_and_login(client, "test_g4_user_a")
    token2 = _register_and_login(client, "test_g4_user_b")
    kb_id = _create_kb(client, token2, "test_g4_b_kb")
    resp = client.get(
        f"/api/kbs/{kb_id}/qa-logs",
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert resp.status_code == 403


# ---- 埋点 + 查询 ----

def test_ask_then_log_exists(client: TestClient) -> None:
    token = _register_and_login(client, "test_g4_log")
    kb_id = _create_kb(client, token, "test_g4_log_kb")
    upload = _upload(client, kb_id, token, "policy.txt",
                     "出差住宿标准：一线城市每晚不超过六百元。".encode("utf-8"))
    assert upload.status_code == 201
    doc_id = upload.json()["id"]

    ask = _ask(client, kb_id, token, "出差住宿标准是什么")
    assert ask.status_code == 200

    resp = client.get(
        f"/api/kbs/{kb_id}/qa-logs",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    logs = resp.json()
    assert len(logs) == 1
    log = logs[0]
    assert log["query"] == "出差住宿标准是什么"
    assert log["answer"]
    assert doc_id in log["hit_doc_ids"]
    assert log["hit_count"] == 1
    assert log["created_at"]


def test_stream_ask_also_logs(client: TestClient) -> None:
    token = _register_and_login(client, "test_g4_stream")
    kb_id = _create_kb(client, token, "test_g4_stream_kb")
    _upload(client, kb_id, token, "policy.txt",
            "出差住宿标准：一线城市每晚不超过六百元。".encode("utf-8"))

    resp = client.post(
        f"/api/kbs/{kb_id}/ask/stream",
        json={"query": "出差住宿"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    logs = client.get(
        f"/api/kbs/{kb_id}/qa-logs",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert len(logs) == 1


def test_empty_kb_ask_logged_with_zero_hits(client: TestClient) -> None:
    token = _register_and_login(client, "test_g4_empty")
    kb_id = _create_kb(client, token, "test_g4_empty_kb")

    ask = _ask(client, kb_id, token, "随便问问")
    assert ask.status_code == 200

    logs = client.get(
        f"/api/kbs/{kb_id}/qa-logs",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert len(logs) == 1
    assert logs[0]["hit_doc_ids"] == []
    assert logs[0]["hit_count"] == 0


def test_limit_and_ordering(client: TestClient) -> None:
    token = _register_and_login(client, "test_g4_limit")
    kb_id = _create_kb(client, token, "test_g4_limit_kb")
    _upload(client, kb_id, token, "policy.txt",
            "出差住宿标准：一线城市每晚不超过六百元。".encode("utf-8"))

    for q in ["问题一", "问题二", "问题三"]:
        _ask(client, kb_id, token, q)

    # 默认 limit=20 → 3 条，按时间倒序（最新在前）
    logs = client.get(
        f"/api/kbs/{kb_id}/qa-logs",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert len(logs) == 3
    assert logs[0]["query"] == "问题三"

    # limit=2 → 只返回最近 2 条
    logs2 = client.get(
        f"/api/kbs/{kb_id}/qa-logs?limit=2",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert len(logs2) == 2
    assert [l["query"] for l in logs2] == ["问题三", "问题二"]
