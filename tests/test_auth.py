"""B2：鉴权测试（注册、登录、JWT、me、登出）。

依赖 PostgreSQL（users 表），PG 不可达时自动跳过。
清理用原生 psycopg2（绕过 SQLAlchemy immutabledict 与 Python 3.13 的兼容问题）。
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


@pytest.fixture(scope="module")
def client() -> TestClient:
    if not _pg_reachable():
        pytest.skip("PostgreSQL 不可达，跳过 B2 鉴权测试（需 users 表）")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(autouse=True)
def _cleanup():
    """测试结束后清理测试用户，避免污染 DB。"""
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
    conn.cursor().execute("DELETE FROM users WHERE username LIKE 'test_%'")
    conn.close()


def test_register(client: TestClient) -> None:
    resp = client.post("/api/auth/register", json={"username": "test_alice", "password": "secret123"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "test_alice"
    assert body["id"]
    assert body["created_at"]


def test_register_duplicate(client: TestClient) -> None:
    client.post("/api/auth/register", json={"username": "test_bob", "password": "secret123"})
    resp = client.post("/api/auth/register", json={"username": "test_bob", "password": "secret456"})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "用户名已存在"


def test_login_success(client: TestClient) -> None:
    client.post("/api/auth/register", json={"username": "test_carol", "password": "secret123"})
    resp = client.post("/api/auth/login", json={"username": "test_carol", "password": "secret123"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"


def test_login_wrong_password(client: TestClient) -> None:
    client.post("/api/auth/register", json={"username": "test_dave", "password": "secret123"})
    resp = client.post("/api/auth/login", json={"username": "test_dave", "password": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "用户名或密码错误"


def test_me_with_valid_token(client: TestClient) -> None:
    client.post("/api/auth/register", json={"username": "test_eve", "password": "secret123"})
    token = client.post("/api/auth/login", json={"username": "test_eve", "password": "secret123"}).json()["access_token"]
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "test_eve"


def test_me_with_invalid_token(client: TestClient) -> None:
    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer bad.token.here"})
    assert resp.status_code == 401


def test_me_without_token(client: TestClient) -> None:
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401  # HTTPBearer 无凭据返回 401


def test_logout(client: TestClient) -> None:
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}