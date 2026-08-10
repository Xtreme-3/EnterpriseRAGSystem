"""B3：知识库 CRUD 测试（创建、列表、详情、删除、权限隔离）。

依赖 PostgreSQL。PG 不可达时自动跳过。
"""
from __future__ import annotations

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
    """注册用户并返回 JWT access_token。"""
    client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    return client.post("/api/auth/login", json={"username": username, "password": "secret123"}).json()["access_token"]


@pytest.fixture(scope="module")
def client() -> TestClient:
    if not _pg_reachable():
        pytest.skip("PostgreSQL 不可达，跳过 B3 知识库测试")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(autouse=True)
def _cleanup():
    """测试结束后清理知识库和用户，避免污染 DB。"""
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


# ---- B3-1: 创建知识库 ----

def test_create_kb(client: TestClient) -> None:
    token = _register_and_login(client, "test_kb_alice")
    resp = client.post("/api/kbs", json={"name": "test_产品手册", "description": "产品相关文档"},
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "test_产品手册"
    assert body["description"] == "产品相关文档"
    assert body["id"]
    assert body["created_at"]
    assert body["document_count"] == 0


def test_create_kb_requires_auth(client: TestClient) -> None:
    resp = client.post("/api/kbs", json={"name": "test_noauth"})
    assert resp.status_code == 401


def test_create_kb_empty_name(client: TestClient) -> None:
    token = _register_and_login(client, "test_kb_bob")
    resp = client.post("/api/kbs", json={"name": "", "description": "x"},
                       headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 422


# ---- B3-2: 列表与详情 ----

def test_list_kbs_only_own(client: TestClient) -> None:
    token_alice = _register_and_login(client, "test_kb_alice2")
    token_bob = _register_and_login(client, "test_kb_bob2")

    client.post("/api/kbs", json={"name": "test_alice_kb"}, headers={"Authorization": f"Bearer {token_alice}"})
    client.post("/api/kbs", json={"name": "test_bob_kb"}, headers={"Authorization": f"Bearer {token_bob}"})

    alice_list = client.get("/api/kbs", headers={"Authorization": f"Bearer {token_alice}"}).json()
    bob_list = client.get("/api/kbs", headers={"Authorization": f"Bearer {token_bob}"}).json()

    alice_names = [k["name"] for k in alice_list]
    bob_names = [k["name"] for k in bob_list]
    assert "test_alice_kb" in alice_names
    assert "test_bob_kb" not in alice_names
    assert "test_bob_kb" in bob_names
    assert "test_alice_kb" not in bob_names


def test_get_kb_detail(client: TestClient) -> None:
    token = _register_and_login(client, "test_kb_detail")
    kb = client.post("/api/kbs", json={"name": "test_detail_kb", "description": "详情测试"},
                     headers={"Authorization": f"Bearer {token}"}).json()

    resp = client.get(f"/api/kbs/{kb['id']}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "test_detail_kb"
    assert body["description"] == "详情测试"
    assert body["documents"] == []
    assert body["document_count"] == 0


def test_get_kb_not_found(client: TestClient) -> None:
    token = _register_and_login(client, "test_kb_404")
    resp = client.get("/api/kbs/99999", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_get_kb_other_user_forbidden(client: TestClient) -> None:
    token_alice = _register_and_login(client, "test_kb_cross1")
    token_bob = _register_and_login(client, "test_kb_cross2")

    kb = client.post("/api/kbs", json={"name": "test_alice_private"},
                     headers={"Authorization": f"Bearer {token_alice}"}).json()

    # Bob 试图访问 Alice 的知识库
    resp = client.get(f"/api/kbs/{kb['id']}", headers={"Authorization": f"Bearer {token_bob}"})
    assert resp.status_code == 403


# ---- B3-3: 删除 ----

def test_delete_kb(client: TestClient) -> None:
    token = _register_and_login(client, "test_kb_delete")
    kb = client.post("/api/kbs", json={"name": "test_to_delete"},
                     headers={"Authorization": f"Bearer {token}"}).json()

    resp = client.delete(f"/api/kbs/{kb['id']}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}

    # 确认已删除
    resp2 = client.get(f"/api/kbs/{kb['id']}", headers={"Authorization": f"Bearer {token}"})
    assert resp2.status_code == 404


def test_delete_kb_other_user_forbidden(client: TestClient) -> None:
    token_alice = _register_and_login(client, "test_kb_del_cross1")
    token_bob = _register_and_login(client, "test_kb_del_cross2")

    kb = client.post("/api/kbs", json={"name": "test_alice_to_delete"},
                     headers={"Authorization": f"Bearer {token_alice}"}).json()

    resp = client.delete(f"/api/kbs/{kb['id']}", headers={"Authorization": f"Bearer {token_bob}"})
    assert resp.status_code == 403

    # 确认 Alice 的库还在
    resp2 = client.get(f"/api/kbs/{kb['id']}", headers={"Authorization": f"Bearer {token_alice}"})
    assert resp2.status_code == 200


def test_delete_kb_requires_auth(client: TestClient) -> None:
    resp = client.delete("/api/kbs/1")
    assert resp.status_code == 401
