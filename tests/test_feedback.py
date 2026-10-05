"""K5 答案反馈测试 —— 全部**离线可跑**（DATA_DIR → tmp_path，mock 供应商）。

链路：注册登录 → 建库 → 建会话 → /ask 生成一轮（空库也落库 user/assistant 两条消息）
→ 对 assistant 消息 POST feedback → 断言 upsert / 清除 / 校验 / 权限 / 列表。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


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


def _conv_and_assistant_msg(
    client: TestClient, kb_id: int, token: str
) -> tuple[int, int, int]:
    """建会话并问一轮（空库也落库），返回 (conv_id, assistant_msg_id, user_msg_id)。"""
    conv = client.post(
        f"/api/kbs/{kb_id}/conversations", json={}, headers=_auth(token)
    ).json()
    resp = client.post(
        f"/api/kbs/{kb_id}/ask",
        json={"query": "随便问点什么", "conversation_id": conv["id"]},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    # K5：AskResponse 直接带回本轮 assistant 消息 id（前端流式 done 事件同源）
    assert resp.json()["message_id"] is not None
    detail = client.get(
        f"/api/kbs/{kb_id}/conversations/{conv['id']}", headers=_auth(token)
    ).json()
    by_role = {m["role"]: m for m in detail["messages"]}
    assert "assistant" in by_role and "user" in by_role
    return conv["id"], by_role["assistant"]["id"], by_role["user"]["id"]


def _setup(client: TestClient, username: str) -> tuple[str, int, int, int]:
    token = _register_and_login(client, username)
    kb_id = client.post(
        "/api/kbs", json={"name": f"test_{username}_kb"}, headers=_auth(token)
    ).json()["id"]
    conv_id, msg_id, user_msg_id = _conv_and_assistant_msg(client, kb_id, token)
    return token, kb_id, msg_id, user_msg_id


def test_feedback_upsert_toggle_and_detail(client: TestClient) -> None:
    """up → down+原因（更新同一票）→ rating=null 清除；会话详情始终带当前用户反馈。"""
    token, kb_id, msg_id, _ = _setup(client, "test_k5_upsert")
    url = f"/api/kbs/{kb_id}/messages/{msg_id}/feedback"

    r1 = client.post(url, json={"rating": "up"}, headers=_auth(token))
    assert r1.status_code == 200
    assert r1.json() == {"message_id": msg_id, "rating": "up", "reason": None}

    r2 = client.post(url, json={"rating": "down", "reason": "答非所问"}, headers=_auth(token))
    assert r2.status_code == 200
    assert r2.json()["rating"] == "down" and r2.json()["reason"] == "答非所问"

    r3 = client.post(url, json={"rating": None}, headers=_auth(token))
    assert r3.status_code == 200
    assert r3.json()["rating"] is None and r3.json()["reason"] is None

    # 重评 down 再查会话详情：feedback 状态随消息返回
    client.post(url, json={"rating": "down", "reason": "引用有误"}, headers=_auth(token))
    conv_id = client.get(
        f"/api/kbs/{kb_id}/conversations", headers=_auth(token)
    ).json()[0]["id"]
    detail = client.get(
        f"/api/kbs/{kb_id}/conversations/{conv_id}", headers=_auth(token)
    ).json()
    msg = next(m for m in detail["messages"] if m["id"] == msg_id)
    assert msg["feedback"] == "down"
    assert msg["feedback_reason"] == "引用有误"


def test_feedback_done_event_carries_message_id(client: TestClient) -> None:
    """流式主路径：done 事件带 assistant message_id，前端才能对刚回答的这条提反馈。"""
    token, kb_id, msg_id, _ = _setup(client, "test_k5_done_id")
    resp = client.post(
        f"/api/kbs/{kb_id}/ask/stream",
        json={"query": "再问一句", "conversation_id": _last_conv_id(client, kb_id, token)},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    done_payloads = [
        line[6:]
        for line in resp.text.split("\n\n")
        if line.startswith("data: ") and '"type": "done"' in line
    ]
    assert done_payloads, "缺少 done 事件"
    payload = __import__("json").loads(done_payloads[-1])
    assert payload["message_id"] is not None and payload["message_id"] > msg_id


def _last_conv_id(client: TestClient, kb_id: int, token: str) -> int:
    return client.get(f"/api/kbs/{kb_id}/conversations", headers=_auth(token)).json()[0]["id"]


def test_feedback_invalid_reason_422(client: TestClient) -> None:
    """reason 必须在固定标签清单内（自由文本聚不起来，故意不收）。"""
    token, kb_id, msg_id, _ = _setup(client, "test_k5_reason")
    resp = client.post(
        f"/api/kbs/{kb_id}/messages/{msg_id}/feedback",
        json={"rating": "down", "reason": "瞎写的原因"},
        headers=_auth(token),
    )
    assert resp.status_code == 422


def test_feedback_invalid_rating_422(client: TestClient) -> None:
    token, kb_id, msg_id, _ = _setup(client, "test_k5_rating")
    resp = client.post(
        f"/api/kbs/{kb_id}/messages/{msg_id}/feedback",
        json={"rating": "mid"},
        headers=_auth(token),
    )
    assert resp.status_code == 422


def test_feedback_rejects_user_message_400(client: TestClient) -> None:
    """只评 AI 回答：对 user 消息提反馈 → 400。"""
    token, kb_id, _, user_msg_id = _setup(client, "test_k5_usermsg")
    resp = client.post(
        f"/api/kbs/{kb_id}/messages/{user_msg_id}/feedback",
        json={"rating": "up"},
        headers=_auth(token),
    )
    assert resp.status_code == 400


def test_feedback_message_not_found_404(client: TestClient) -> None:
    token, kb_id, _, _ = _setup(client, "test_k5_404")
    resp = client.post(
        f"/api/kbs/{kb_id}/messages/999999/feedback",
        json={"rating": "up"},
        headers=_auth(token),
    )
    assert resp.status_code == 404


def test_feedback_message_in_wrong_kb_404(client: TestClient) -> None:
    """消息真实存在但挂在别的 KB → 一律 404（不泄露资源存在性）。"""
    token, kb_id, msg_id, _ = _setup(client, "test_k5_cross")
    other_kb = client.post(
        "/api/kbs", json={"name": "test_k5_cross_other"}, headers=_auth(token)
    ).json()["id"]
    resp = client.post(
        f"/api/kbs/{other_kb}/messages/{msg_id}/feedback",
        json={"rating": "up"},
        headers=_auth(token),
    )
    assert resp.status_code == 404


def test_feedback_other_user_403(client: TestClient) -> None:
    """他人（即使只是 viewer 成员）不能评价我的会话消息。"""
    token_a, kb_id, msg_id, _ = _setup(client, "test_k5_owner")
    token_b = _register_and_login(client, "test_k5_viewer")
    resp = client.post(
        f"/api/kbs/{kb_id}/members",
        json={"username": "test_k5_viewer", "role": "viewer"},
        headers=_auth(token_a),
    )
    assert resp.status_code == 201, resp.text
    resp = client.post(
        f"/api/kbs/{kb_id}/messages/{msg_id}/feedback",
        json={"rating": "up"},
        headers=_auth(token_b),
    )
    assert resp.status_code == 403


def test_feedback_list_and_permission(client: TestClient) -> None:
    """GET /feedback：倒序 + 答案预览；非成员 403；limit 越界 422。"""
    token, kb_id, msg_id, _ = _setup(client, "test_k5_list")
    client.post(
        f"/api/kbs/{kb_id}/messages/{msg_id}/feedback",
        json={"rating": "down", "reason": "内容过时"},
        headers=_auth(token),
    )

    resp = client.get(f"/api/kbs/{kb_id}/feedback", headers=_auth(token))
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["rating"] == "down"
    assert items[0]["reason"] == "内容过时"
    assert items[0]["message_id"] == msg_id
    assert items[0]["content_preview"]

    # 非成员不可读
    token_b = _register_and_login(client, "test_k5_list_b")
    assert (
        client.get(f"/api/kbs/{kb_id}/feedback", headers=_auth(token_b)).status_code == 403
    )
    # limit 越界
    assert (
        client.get(
            f"/api/kbs/{kb_id}/feedback?limit=0", headers=_auth(token)
        ).status_code
        == 422
    )
