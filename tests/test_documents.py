"""B4：文档上传 + 向量化测试。

依赖 PostgreSQL + mock embedding provider。
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from tests.ingest_helpers import upload_and_wait_response, upload_raw, wait_document


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


@pytest.fixture(scope="module")
def client() -> TestClient:
    if not _pg_reachable():
        pytest.skip("PostgreSQL 不可达，跳过 B4 文档上传测试")
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


# ---- helpers ----

def _upload(client: TestClient, kb_id: int, token: str, filename: str, content: bytes, content_type: str = "text/plain"):
    """上传并等摄取收敛，返回**终态响应**（K4：接口本身只回 202 pending）。

    需要断言"上传那一刻是 202/pending"的用例请直接用 ``upload_raw``，
    见 ``test_upload_txt``。
    """
    return upload_and_wait_response(client, kb_id, token, filename, content, content_type)


# ---- B4-1: 文档上传 ----

def test_upload_txt(client: TestClient) -> None:
    token = _register_and_login(client, "test_b4_alice")
    kb_id = _create_kb(client, token, "test_b4_kb")

    content = "This is a test document with some content for vectorization.".encode("utf-8")

    # K4 契约：上传**立即 202 + pending**，向量化交给后台（这是本积木的核心行为，
    # 用 upload_raw 拿原始响应，不走"等到终态"的辅助函数）
    resp = upload_raw(client, kb_id, token, "readme.txt", content)
    assert resp.status_code == 202
    body = resp.json()
    assert body["filename"] == "readme.txt"
    assert body["file_type"] == "txt"
    assert body["status"] == "pending"
    assert body["chunk_count"] == 0
    assert body["error"] is None
    assert body["job"]["stage"] == "pending"
    assert body["job"]["total_units"] == 0  # chunking 完成后才回填

    # 后台跑完后转终态
    final = wait_document(client, body["id"], token)
    assert final["status"] == "indexed"
    assert final["chunk_count"] >= 1
    assert final["error"] is None
    assert final["job"]["stage"] == "done"


def test_upload_md(client: TestClient) -> None:
    token = _register_and_login(client, "test_b4_md")
    kb_id = _create_kb(client, token, "test_b4_md_kb")

    md_content = "# 标题\n\n## 章节一\n\n这是第一段内容。\n\n## 章节二\n\n这是第二段内容。".encode("utf-8")
    resp = _upload(client, kb_id, token, "doc.md", md_content, "text/markdown")
    assert resp.json()["file_type"] == "md"
    assert resp.json()["status"] == "indexed"


def test_upload_to_nonexistent_kb(client: TestClient) -> None:
    token = _register_and_login(client, "test_b4_404")
    resp = _upload(client, 99999, token, "doc.txt", b"content")
    assert resp.status_code == 404


def test_upload_to_others_kb(client: TestClient) -> None:
    token_alice = _register_and_login(client, "test_b4_cross1")
    token_bob = _register_and_login(client, "test_b4_cross2")
    kb_id = _create_kb(client, token_alice, "test_b4_alice_kb")

    # Bob 尝试上传到 Alice 的 KB
    resp = _upload(client, kb_id, token_bob, "doc.txt", b"malicious")
    assert resp.status_code == 403


def test_upload_requires_auth(client: TestClient) -> None:
    resp = _upload(client, 1, "", "doc.txt", b"x")
    # 注意：空 token 会导致 HTTPBearer 返回 401（无有效 Authorization header）
    resp2 = client.post("/api/kbs/1/documents", files={"file": ("doc.txt", io.BytesIO(b"x"), "text/plain")})
    assert resp2.status_code == 401


def test_upload_unsupported_type(client: TestClient) -> None:
    token = _register_and_login(client, "test_b4_badtype")
    kb_id = _create_kb(client, token, "test_b4_badtype_kb")

    resp = _upload(client, kb_id, token, "image.png", b"\x89PNG\r\n\x1a\n")
    assert resp.status_code == 400
    assert "不支持" in resp.json()["detail"]


def test_upload_empty_file(client: TestClient) -> None:
    token = _register_and_login(client, "test_b4_empty")
    kb_id = _create_kb(client, token, "test_b4_empty_kb")

    resp = _upload(client, kb_id, token, "empty.txt", b"")
    assert resp.status_code == 400


# ---- B4-2: 状态查询 ----

def test_get_document_status(client: TestClient) -> None:
    token = _register_and_login(client, "test_b4_status")
    kb_id = _create_kb(client, token, "test_b4_status_kb")

    doc = _upload(client, kb_id, token, "status.txt", "Status check test document with enough content to generate chunks for the vector store pipeline verification.".encode("utf-8")).json()
    doc_id = doc["id"]

    resp = client.get(f"/api/documents/{doc_id}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == doc_id
    assert body["filename"] == "status.txt"
    assert body["status"] == "indexed"
    assert body["chunk_count"] >= 1


def test_get_document_not_found(client: TestClient) -> None:
    token = _register_and_login(client, "test_b4_doc404")
    resp = client.get("/api/documents/99999", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_get_document_other_user_kb(client: TestClient) -> None:
    token_alice = _register_and_login(client, "test_b4_doc_cross1")
    token_bob = _register_and_login(client, "test_b4_doc_cross2")
    kb_id = _create_kb(client, token_alice, "test_b4_doc_cross_kb")

    doc = _upload(client, kb_id, token_alice, "secret.txt", "Confidential content that should not be accessible to others.".encode("utf-8")).json()

    # Bob 试图查看 Alice 的文档
    resp = client.get(f"/api/documents/{doc['id']}", headers={"Authorization": f"Bearer {token_bob}"})
    assert resp.status_code == 403


# ---- B5-1: 文档列表 ----


def test_list_documents(client: TestClient) -> None:
    token = _register_and_login(client, "test_b5_list")
    kb_id = _create_kb(client, token, "test_b5_list_kb")

    _upload(client, kb_id, token, "a.txt", b"doc a content here for testing")
    _upload(client, kb_id, token, "b.md", b"# md doc\n\nmore content here for the second doc")

    resp = client.get(f"/api/kbs/{kb_id}/documents", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["filename"] == "b.md"  # 按时间倒序，b 在上
    assert body[1]["filename"] == "a.txt"
    for doc in body:
        assert doc["status"] == "indexed"
        assert doc["chunk_count"] >= 1


def test_list_documents_empty(client: TestClient) -> None:
    token = _register_and_login(client, "test_b5_empty")
    kb_id = _create_kb(client, token, "test_b5_empty_kb")

    resp = client.get(f"/api/kbs/{kb_id}/documents", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_documents_cross_user(client: TestClient) -> None:
    token_alice = _register_and_login(client, "test_b5_list_cross1")
    token_bob = _register_and_login(client, "test_b5_list_cross2")
    kb_id = _create_kb(client, token_alice, "test_b5_list_alice_kb")

    # Bob 不能看 Alice 的知识库文档列表
    resp = client.get(f"/api/kbs/{kb_id}/documents", headers={"Authorization": f"Bearer {token_bob}"})
    assert resp.status_code == 403


# ---- B5-2: 文档删除 ----


def test_delete_document(client: TestClient) -> None:
    token = _register_and_login(client, "test_b5_del")
    kb_id = _create_kb(client, token, "test_b5_del_kb")
    doc = _upload(client, kb_id, token, "to_delete.txt", b"delete me please").json()

    resp = client.delete(f"/api/documents/{doc['id']}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "deleted"}

    # 确认已删除
    resp2 = client.get(f"/api/documents/{doc['id']}", headers={"Authorization": f"Bearer {token}"})
    assert resp2.status_code == 404


def test_delete_document_not_found(client: TestClient) -> None:
    token = _register_and_login(client, "test_b5_del404")
    resp = client.delete("/api/documents/99999", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404


def test_delete_document_cross_user(client: TestClient) -> None:
    token_alice = _register_and_login(client, "test_b5_del_cross1")
    token_bob = _register_and_login(client, "test_b5_del_cross2")
    kb_id = _create_kb(client, token_alice, "test_b5_del_cross_kb")
    doc = _upload(client, kb_id, token_alice, "keep.txt", b"hands off").json()

    # Bob 不能删除 Alice 的文档
    resp = client.delete(f"/api/documents/{doc['id']}", headers={"Authorization": f"Bearer {token_bob}"})
    assert resp.status_code == 403

    # 确认 Alice 的文档还在
    resp2 = client.get(f"/api/documents/{doc['id']}", headers={"Authorization": f"Bearer {token_alice}"})
    assert resp2.status_code == 200
