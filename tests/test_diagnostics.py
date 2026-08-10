"""G2：摄取诊断 API 测试。依赖 PostgreSQL + mock provider。"""
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


@pytest.fixture(scope="module")
def client() -> TestClient:
    if not _pg_reachable():
        pytest.skip("PostgreSQL 不可达，跳过 G2 摄取诊断测试")
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
    cur.execute("DELETE FROM knowledge_bases WHERE name LIKE 'test_g2%'")
    cur.execute("DELETE FROM users WHERE username LIKE 'test_g2%'")
    conn.close()


# ---- Auth ----

def test_no_token_returns_401(client: TestClient) -> None:
    resp = client.get("/api/kbs/1/diagnostics")
    assert resp.status_code == 401


def test_other_user_kb_returns_403(client: TestClient) -> None:
    token1 = _register_and_login(client, "test_g2_user_a")
    token2 = _register_and_login(client, "test_g2_user_b")
    kb_id = _create_kb(client, token2, "test_g2_b_kb")
    resp = client.get(
        f"/api/kbs/{kb_id}/diagnostics",
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert resp.status_code == 403


# ---- Endpoint ----

def test_empty_kb_diagnostics(client: TestClient) -> None:
    token = _register_and_login(client, "test_g2_empty")
    kb_id = _create_kb(client, token, "test_g2_empty_kb")
    resp = client.get(
        f"/api/kbs/{kb_id}/diagnostics",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["kb_id"] == kb_id
    s = data["summary"]
    assert s == {"total_documents": 0, "indexed": 0, "failed": 0,
                 "processing": 0, "pending": 0, "total_chunks": 0}
    assert data["failures"] == []
    assert data["anomalies"] == []


def test_diagnostics_with_indexed_doc(client: TestClient) -> None:
    token = _register_and_login(client, "test_g2_ok")
    kb_id = _create_kb(client, token, "test_g2_ok_kb")
    _upload(client, kb_id, token, "policy.txt",
            "出差住宿标准：一线城市每晚不超过六百元。年假满一年享五天。".encode("utf-8"))

    resp = client.get(
        f"/api/kbs/{kb_id}/diagnostics",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    s = data["summary"]
    assert s["total_documents"] == 1
    assert s["indexed"] == 1
    assert s["failed"] == 0
    assert s["total_chunks"] > 0
    assert data["failures"] == []
    assert data["anomalies"] == []


def test_diagnostics_with_failed_doc(client: TestClient) -> None:
    """上传空白内容 txt → 解析后无有效内容 → 置 failed → 体检能聚合失败原因。"""
    token = _register_and_login(client, "test_g2_fail")
    kb_id = _create_kb(client, token, "test_g2_fail_kb")

    resp = _upload(client, kb_id, token, "blank.txt", b"   \n  \t  ")
    assert resp.status_code == 201
    assert resp.json()["status"] == "failed"

    resp = client.get(
        f"/api/kbs/{kb_id}/diagnostics",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    s = data["summary"]
    assert s["total_documents"] == 1
    assert s["indexed"] == 0
    assert s["failed"] == 1

    assert len(data["failures"]) == 1
    group = data["failures"][0]
    assert group["error"]  # 非空（未知错误也归并）
    assert group["count"] == 1
    assert group["documents"][0]["filename"] == "blank.txt"

    # 异常清单包含该 failed 文档
    assert any(a["filename"] == "blank.txt" and a["status"] == "failed" for a in data["anomalies"])


# ---- G3: 向量一致性 ----

def _vector_store():
    from app.config import get_settings
    from app.storage.vector_store import build_vector_store

    return build_vector_store(get_settings())


def test_consistency_clean_after_index(client: TestClient) -> None:
    """正常摄取后一致性 checked=True 且无问题。"""
    token = _register_and_login(client, "test_g3_clean")
    kb_id = _create_kb(client, token, "test_g3_clean_kb")
    _upload(client, kb_id, token, "policy.txt",
            "出差住宿标准：一线城市每晚不超过六百元。".encode("utf-8"))

    resp = client.get(f"/api/kbs/{kb_id}/diagnostics", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    cons = resp.json()["consistency"]
    assert cons["checked"] is True
    assert cons["issues"] == []


def test_consistency_missing_index(client: TestClient) -> None:
    """上传成功后手动删向量 → 报 missing_index。"""
    token = _register_and_login(client, "test_g3_miss")
    kb_id = _create_kb(client, token, "test_g3_miss_kb")
    resp = _upload(client, kb_id, token, "policy.txt",
                   "差旅住宿标准：一线城市每晚不超过六百元。".encode("utf-8"))
    assert resp.status_code == 201
    doc_id = resp.json()["id"]

    # 模拟漂移：向量库里该文档的向量被删除
    _vector_store().delete_document(kb_id, doc_id)

    resp = client.get(f"/api/kbs/{kb_id}/diagnostics", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    cons = resp.json()["consistency"]
    assert cons["checked"] is True
    issue = next((i for i in cons["issues"] if i["document_id"] == doc_id), None)
    assert issue is not None
    assert issue["kind"] == "missing_index"
    assert issue["meta_count"] > 0
    assert issue["vector_count"] == 0


def test_consistency_orphan_vector(client: TestClient) -> None:
    """向量库残留元数据里不存在的文档向量 → 报 orphan_vector。"""
    token = _register_and_login(client, "test_g3_orphan")
    kb_id = _create_kb(client, token, "test_g3_orphan_kb")

    # 直接往向量库塞一条不存在文档的向量
    from app.config import get_settings
    from app.providers.factory import build_embedding
    from app.storage.vector_store import ChunkToIndex

    dim = build_embedding(get_settings()).dim
    store = _vector_store()
    store.ensure_collection(kb_id, dim)
    orphan_doc = 999_991
    store.add(
        kb_id,
        [
            ChunkToIndex(
                id=f"{kb_id}:orphan:0", kb_id=kb_id, document_id=orphan_doc,
                chunk_index=0, content="孤儿向量", vector=[1.0] + [0.0] * (dim - 1),
            )
        ],
    )
    try:
        resp = client.get(f"/api/kbs/{kb_id}/diagnostics", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        cons = resp.json()["consistency"]
        assert cons["checked"] is True
        issue = next((i for i in cons["issues"] if i["document_id"] == orphan_doc), None)
        assert issue is not None
        assert issue["kind"] == "orphan_vector"
        assert issue["meta_count"] == 0
        assert issue["vector_count"] == 1
    finally:
        store.delete_collection(kb_id)
