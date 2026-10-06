"""G6 数据看板聚合测试 —— 全部**离线可跑**（DATA_DIR → tmp_path，mock 供应商）。

数据用真实 API 流程灌入（提问 / 反馈 / 传文档），再断言聚合口径。
缓存口径注意：多轮（会话非空）的提问不查不写缓存——第 5 问是会话第 2 轮，
不参与命中，所以 cache_hits = 3 而不是 4。
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app.main import app

Q = "出差住宿标准是多少？"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _register_and_login(client: TestClient, username: str) -> str:
    client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    return client.post(
        "/api/auth/login", json={"username": username, "password": "secret123"}
    ).json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_dashboard_aggregates_real_flow(client: TestClient, tmp_path) -> None:
    token = _register_and_login(client, "test_g6_agg")
    kb_id = client.post(
        "/api/kbs", json={"name": "test_g6_kb"}, headers=_auth(token)
    ).json()["id"]

    # 死文档：真实上传会与缓存口径耦合（成功上传触发 K6 按库失效；可检索的文档
    # 又会被提问命中）——直接种子一行 failed 文档（无切片、永不被命中、不动缓存）
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "data" / "rag.db"))
    conn.execute(
        "INSERT INTO documents (kb_id, filename, file_type, status, chunk_count, created_at)"
        " VALUES (?, 'broken.pdf', 'pdf', 'failed', 0, datetime('now'))",
        (kb_id,),
    )
    conn.commit()
    conn.close()

    # 前三问（无会话）：#1 未命中并写入缓存，#2/#3 命中
    for _ in range(3):
        r = client.post(f"/api/kbs/{kb_id}/ask", json={"query": Q}, headers=_auth(token))
        assert r.status_code == 200

    # 后两问走会话并落消息 → 可反馈；第 5 问会话历史非空，不查不写缓存
    conv = client.post(
        f"/api/kbs/{kb_id}/conversations", json={}, headers=_auth(token)
    ).json()
    msg_ids = []
    for _ in range(2):
        r = client.post(
            f"/api/kbs/{kb_id}/ask",
            json={"query": Q, "conversation_id": conv["id"]},
            headers=_auth(token),
        )
        assert r.status_code == 200
        msg_ids.append(r.json()["message_id"])

    client.post(
        f"/api/kbs/{kb_id}/messages/{msg_ids[0]}/feedback",
        json={"rating": "down", "reason": "答非所问"},
        headers=_auth(token),
    )
    client.post(
        f"/api/kbs/{kb_id}/messages/{msg_ids[1]}/feedback",
        json={"rating": "up"},
        headers=_auth(token),
    )

    resp = client.get(f"/api/kbs/{kb_id}/dashboard", headers=_auth(token))
    assert resp.status_code == 200
    d = resp.json()

    assert d["total_questions"] == 5
    assert d["cache_hits"] == 3  # 第 5 问会话历史非空，不查不写缓存
    assert d["cache_hit_rate"] == 0.6
    assert d["feedback_total"] == 2
    assert d["feedback_up"] == 1
    assert d["feedback_up_rate"] == 0.5
    assert d["total_documents"] == 1
    assert d["dead_documents"] == 1  # failed 文档无切片，永远不可能被命中
    assert d["top_questions"] == [
        {"query": Q, "count": 5, "cache_hits": 3}
    ]
    assert d["reason_breakdown"] == [{"reason": "答非所问", "count": 1}]
    assert d["dead_doc_list"] == [
        {"filename": "broken.pdf", "status": "failed"}
    ]


def test_dashboard_empty_kb_all_zero(client: TestClient) -> None:
    token = _register_and_login(client, "test_g6_empty")
    kb_id = client.post(
        "/api/kbs", json={"name": "test_g6_empty_kb"}, headers=_auth(token)
    ).json()["id"]

    resp = client.get(f"/api/kbs/{kb_id}/dashboard", headers=_auth(token))
    assert resp.status_code == 200
    d = resp.json()

    assert d["total_questions"] == 0
    assert d["cache_hits"] == 0
    assert d["cache_hit_rate"] == 0.0
    assert d["feedback_total"] == 0
    assert d["feedback_up_rate"] == 0.0
    assert d["dead_documents"] == 0
    assert d["top_questions"] == []
    assert d["reason_breakdown"] == []
    assert d["dead_doc_list"] == []


def test_dashboard_forbidden_for_non_member(client: TestClient) -> None:
    token_a = _register_and_login(client, "test_g6_owner")
    kb_id = client.post(
        "/api/kbs", json={"name": "test_g6_owner_kb"}, headers=_auth(token_a)
    ).json()["id"]
    token_b = _register_and_login(client, "test_g6_stranger")

    resp = client.get(f"/api/kbs/{kb_id}/dashboard", headers=_auth(token_b))
    assert resp.status_code == 403
