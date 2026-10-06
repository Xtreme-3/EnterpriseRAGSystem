"""G1：检索质检台 API 测试。

依赖 PostgreSQL + mock provider。
"""
from __future__ import annotations


import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from tests.ingest_helpers import upload_and_wait_response


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
    """上传并等摄取收敛，返回**终态响应**（K4：上传接口只回 202 pending）。"""
    return upload_and_wait_response(client, kb_id, token, filename, content)


@pytest.fixture(scope="module")
def client() -> TestClient:
    if not _pg_reachable():
        pytest.skip("PostgreSQL 不可达，跳过 G1 检索质检测试")
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
    cur.execute(
        "DELETE FROM knowledge_bases "
        "WHERE name LIKE 'test_g1%' OR name LIKE 'test_h1%' OR name LIKE 'test_i1%'"
    )
    cur.execute(
        "DELETE FROM users "
        "WHERE username LIKE 'test_g1%' OR username LIKE 'test_h1%' OR username LIKE 'test_i1%'"
    )
    conn.close()


# ---- Auth ----

def test_no_token_returns_401(client: TestClient) -> None:
    resp = client.post("/api/kbs/1/inspect", json={"query": "test"})
    assert resp.status_code == 401


def test_other_user_kb_returns_403(client: TestClient) -> None:
    token1 = _register_and_login(client, "test_g1_user_a")
    token2 = _register_and_login(client, "test_g1_user_b")
    kb_id = _create_kb(client, token2, "test_g1_b_kb")
    resp = client.post(
        f"/api/kbs/{kb_id}/inspect",
        json={"query": "test"},
        headers={"Authorization": f"Bearer {token1}"},
    )
    assert resp.status_code == 403


# ---- Endpoint ----

def test_empty_kb_returns_empty_hits(client: TestClient) -> None:
    token = _register_and_login(client, "test_g1_empty")
    kb_id = _create_kb(client, token, "test_g1_empty_kb")
    resp = client.post(
        f"/api/kbs/{kb_id}/inspect",
        json={"query": "test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "test"
    assert data["kb_id"] == kb_id
    assert data["hits"] == []


def test_inspect_structure_no_answer(client: TestClient) -> None:
    token = _register_and_login(client, "test_g1_struct")
    kb_id = _create_kb(client, token, "test_g1_struct_kb")
    resp = client.post(
        f"/api/kbs/{kb_id}/inspect",
        json={"query": "hello"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" not in data
    for key in ("hits", "query", "kb_id", "top_k"):
        assert key in data


def test_top_k_custom(client: TestClient) -> None:
    token = _register_and_login(client, "test_g1_topk")
    kb_id = _create_kb(client, token, "test_g1_topk_kb")
    resp = client.post(
        f"/api/kbs/{kb_id}/inspect",
        json={"query": "test", "top_k": 3},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["top_k"] == 3


def test_top_k_exceeds_limit(client: TestClient) -> None:
    token = _register_and_login(client, "test_g1_limit")
    kb_id = _create_kb(client, token, "test_g1_limit_kb")
    resp = client.post(
        f"/api/kbs/{kb_id}/inspect",
        json={"query": "test", "top_k": 100},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_empty_query_rejected(client: TestClient) -> None:
    token = _register_and_login(client, "test_g1_emptyq")
    kb_id = _create_kb(client, token, "test_g1_emptyq_kb")
    resp = client.post(
        f"/api/kbs/{kb_id}/inspect",
        json={"query": "", "top_k": 5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


# ---- With indexed docs ----

def test_inspect_with_indexed_doc(client: TestClient) -> None:
    token = _register_and_login(client, "test_g1_docs")
    kb_id = _create_kb(client, token, "test_g1_docs_kb")
    _upload(client, kb_id, token, "ai_intro.txt",
            "人工智能是计算机科学的一个分支，致力于创建能够模拟人类智能的系统。".encode("utf-8"))

    resp = client.post(
        f"/api/kbs/{kb_id}/inspect",
        json={"query": "人工智能", "top_k": 3},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["hits"]) > 0
    hit = data["hits"][0]
    assert hit["filename"] == "ai_intro.txt"
    for field in ("document_id", "filename", "chunk_index", "content", "score"):
        assert field in hit
    assert isinstance(hit["score"], float)
    # 验证分数降序
    scores = [h["score"] for h in data["hits"]]
    assert scores == sorted(scores, reverse=True)


# ---- H1: 检索模式 mode ----

def test_inspect_returns_default_mode(client: TestClient) -> None:
    token = _register_and_login(client, "test_h1_mode_default")
    kb_id = _create_kb(client, token, "test_h1_mode_default_kb")
    resp = client.post(
        f"/api/kbs/{kb_id}/inspect",
        json={"query": "test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "hybrid"  # 服务端默认 retrieval_mode


def test_inspect_mode_vector(client: TestClient) -> None:
    token = _register_and_login(client, "test_h1_mode_vec")
    kb_id = _create_kb(client, token, "test_h1_mode_vec_kb")
    resp = client.post(
        f"/api/kbs/{kb_id}/inspect",
        json={"query": "test", "mode": "vector"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "vector"


def test_inspect_mode_invalid(client: TestClient) -> None:
    token = _register_and_login(client, "test_h1_mode_bad")
    kb_id = _create_kb(client, token, "test_h1_mode_bad_kb")
    resp = client.post(
        f"/api/kbs/{kb_id}/inspect",
        json={"query": "test", "mode": "weird"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422


def test_inspect_empty_kb_mode_and_hits(client: TestClient) -> None:
    """空库：仍 200，mode 返回，hits 空列表。"""
    token = _register_and_login(client, "test_h1_mode_empty")
    kb_id = _create_kb(client, token, "test_h1_mode_empty_kb")
    resp = client.post(
        f"/api/kbs/{kb_id}/inspect",
        json={"query": "test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "hybrid"
    assert data["hits"] == []


# ---- I1: 重排 rerank ----


def _upload_intro(client: TestClient, kb_id: int, token: str):
    return _upload(
        client, kb_id, token, "ai_intro.txt",
        "人工智能是计算机科学的一个分支，致力于创建能够模拟人类智能的系统。".encode("utf-8"),
    )


def test_inspect_rerank_default_off(client: TestClient) -> None:
    """不传 rerank：用服务端配置（默认 False），pre_score == score（Noop 恒等）。"""
    token = _register_and_login(client, "test_i1_default")
    kb_id = _create_kb(client, token, "test_i1_default_kb")
    _upload_intro(client, kb_id, token)
    resp = client.post(
        f"/api/kbs/{kb_id}/inspect",
        json={"query": "人工智能", "top_k": 3},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rerank"] is False
    assert data["hits"]
    for h in data["hits"]:
        assert "pre_score" in h
        assert h["pre_score"] == h["score"]  # 未重排：重排分即检索分
    scores = [h["score"] for h in data["hits"]]
    assert scores == sorted(scores, reverse=True)


def test_inspect_rerank_explicit_false(client: TestClient) -> None:
    token = _register_and_login(client, "test_i1_off")
    kb_id = _create_kb(client, token, "test_i1_off_kb")
    _upload_intro(client, kb_id, token)
    resp = client.post(
        f"/api/kbs/{kb_id}/inspect",
        json={"query": "人工智能", "rerank": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["rerank"] is False


def test_inspect_rerank_on_returns_reranked_scores(client: TestClient) -> None:
    """rerank=true：响应 rerank=true，命中分数为词重叠重排分（∈[0,1]），pre_score 保留检索分。"""
    token = _register_and_login(client, "test_i1_on")
    kb_id = _create_kb(client, token, "test_i1_on_kb")
    _upload_intro(client, kb_id, token)
    resp = client.post(
        f"/api/kbs/{kb_id}/inspect",
        json={"query": "人工智能", "rerank": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rerank"] is True
    assert data["hits"]
    for h in data["hits"]:
        assert "pre_score" in h
        assert 0.0 <= h["score"] <= 1.0
    scores = [h["score"] for h in data["hits"]]
    assert scores == sorted(scores, reverse=True)
    # 与 query 词重叠最高的切片排最前（query 含"人工智能"）
    assert "人工智能" in data["hits"][0]["content"]


def test_inspect_rerank_empty_kb_still_200(client: TestClient) -> None:
    """空库 + rerank=true：仍 200，rerank=true，hits 空。"""
    token = _register_and_login(client, "test_i1_empty")
    kb_id = _create_kb(client, token, "test_i1_empty_kb")
    resp = client.post(
        f"/api/kbs/{kb_id}/inspect",
        json={"query": "test", "rerank": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["rerank"] is True
    assert data["hits"] == []
