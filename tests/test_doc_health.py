"""G5：文档健康分析测试（死文档 + 命中热力）。依赖 PostgreSQL + mock provider。"""
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
        pytest.skip("PostgreSQL 不可达，跳过 G5 文档健康测试")
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
    cur.execute("DELETE FROM qa_logs WHERE kb_id IN (SELECT id FROM knowledge_bases WHERE name LIKE 'test_g5%')")
    cur.execute("DELETE FROM knowledge_bases WHERE name LIKE 'test_g5%'")
    cur.execute("DELETE FROM users WHERE username LIKE 'test_g5%'")
    conn.close()


# ---- Auth ----

def test_no_token_returns_401(client: TestClient) -> None:
    resp = client.get("/api/kbs/1/doc-health")
    assert resp.status_code == 401


def test_other_user_kb_returns_403(client: TestClient) -> None:
    token1 = _register_and_login(client, "test_g5_user_a")
    token2 = _register_and_login(client, "test_g5_user_b")
    kb_id = _create_kb(client, token2, "test_g5_b_kb")
    resp = client.get(
        f"/api/kbs/{kb_id}/doc-health",
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert resp.status_code == 403


# ---- Endpoint ----

def test_empty_kb_doc_health(client: TestClient) -> None:
    token = _register_and_login(client, "test_g5_empty")
    kb_id = _create_kb(client, token, "test_g5_empty_kb")
    resp = client.get(
        f"/api/kbs/{kb_id}/doc-health",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["kb_id"] == kb_id
    s = data["summary"]
    assert s == {"total_documents": 0, "indexed": 0, "active_count": 0,
                 "dead_count": 0, "total_questions": 0, "hit_rate": 0.0}
    assert data["dead_docs"] == []
    assert data["hot_docs"] == []


def test_hot_doc_and_dead_doc(client: TestClient) -> None:
    """两个文档，只问命中第一个：第一个进 hot_docs，第二个从未命中进 dead_docs。"""
    token = _register_and_login(client, "test_g5_hot")
    kb_id = _create_kb(client, token, "test_g5_hot_kb")
    policy = _upload(client, kb_id, token, "policy.txt",
                     "出差住宿标准：一线城市每晚不超过六百元。".encode("utf-8"))
    policy_id = policy.json()["id"]
    dead = _upload(client, kb_id, token, "canteen.txt",
                   "员工食堂午餐补贴：每人每天二十元。".encode("utf-8"))
    dead_id = dead.json()["id"]

    ask = _ask(client, kb_id, token, "出差住宿标准是什么")
    assert ask.status_code == 200

    resp = client.get(
        f"/api/kbs/{kb_id}/doc-health",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    s = data["summary"]
    assert s["total_documents"] == 2
    assert s["indexed"] == 2
    assert s["total_questions"] == 1
    assert s["active_count"] == 1
    assert s["dead_count"] == 1
    assert s["hit_rate"] == 0.5

    assert len(data["hot_docs"]) == 1
    hot = data["hot_docs"][0]
    assert hot["id"] == policy_id
    assert hot["filename"] == "policy.txt"
    assert hot["hit_count"] == 1
    assert hot["last_hit_at"]

    assert len(data["dead_docs"]) == 1
    dead_doc = data["dead_docs"][0]
    assert dead_doc["id"] == dead_id
    assert dead_doc["filename"] == "canteen.txt"
    assert dead_doc["status"] == "indexed"
    assert dead_doc["hit_count"] == 0


def test_hit_count_accumulates_and_last_hit_updates(client: TestClient) -> None:
    token = _register_and_login(client, "test_g5_acc")
    kb_id = _create_kb(client, token, "test_g5_acc_kb")
    _upload(client, kb_id, token, "policy.txt",
            "出差住宿标准：一线城市每晚不超过六百元。年假满一年享五天。".encode("utf-8"))

    for q in ["出差住宿", "年假"]:
        _ask(client, kb_id, token, q)

    data = client.get(
        f"/api/kbs/{kb_id}/doc-health",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    hot = data["hot_docs"][0]
    assert hot["hit_count"] == 2
    assert data["summary"]["total_questions"] == 2
    assert data["summary"]["active_count"] == 1
    assert data["summary"]["dead_count"] == 0
    assert data["summary"]["hit_rate"] == 1.0


def test_failed_doc_is_dead_with_error(client: TestClient) -> None:
    """空白文件解析失败 → failed → 也归入 dead_docs 且保留 error。"""
    token = _register_and_login(client, "test_g5_fail")
    kb_id = _create_kb(client, token, "test_g5_fail_kb")
    upload = _upload(client, kb_id, token, "blank.txt", b"   \n  \t  ")
    assert upload.status_code == 201
    assert upload.json()["status"] == "failed"

    data = client.get(
        f"/api/kbs/{kb_id}/doc-health",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    s = data["summary"]
    assert s["total_documents"] == 1
    assert s["indexed"] == 0
    assert s["dead_count"] == 1
    assert s["hit_rate"] == 0.0
    assert data["hot_docs"] == []

    dead = data["dead_docs"][0]
    assert dead["status"] == "failed"
    assert dead["chunk_count"] == 0
    assert dead["error"]  # 非空错误文本


def test_limit_controls_hot_docs(client: TestClient) -> None:
    token = _register_and_login(client, "test_g5_limit")
    kb_id = _create_kb(client, token, "test_g5_limit_kb")
    _upload(client, kb_id, token, "policy.txt",
            "出差住宿标准：一线城市每晚不超过六百元。年假满一年享五天。".encode("utf-8"))

    for q in ["出差住宿", "年假"]:
        _ask(client, kb_id, token, q)

    data = client.get(
        f"/api/kbs/{kb_id}/doc-health?limit=0",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert data.status_code == 422  # limit 下限为 1

    data = client.get(
        f"/api/kbs/{kb_id}/doc-health?limit=51",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert data.status_code == 422  # limit 上限为 50

    data = client.get(
        f"/api/kbs/{kb_id}/doc-health?limit=1",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert data.status_code == 200
    assert len(data.json()["hot_docs"]) == 1
