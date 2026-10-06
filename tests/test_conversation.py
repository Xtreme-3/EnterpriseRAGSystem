"""J2：会话持久化测试 —— 会话 CRUD + 归属校验 + ask/ask_stream 落库 + 服务端历史 + 级联删除。

分层：
- 集成（PG 可达才跑，test_multiturn 模式，前缀 test_j2%）：
  - CRUD：创建 201 / 列表 / 详情 / 改名 / 删除，归属 403 / 别库 404 / 未登录 401
  - ask 带 conversation_id：返回 id、落库 user+assistant（assistant 带 sources/rewritten_query）
  - 服务端历史：第二轮追问经库内历史改写（rewritten_query 体现上一轮用户问句）
  - 标题：默认"新对话"，首轮消息后更新为提问前 30 字
  - 顺序：详情消息按 id 正序；列表按 updated_at 倒序
  - 级联删除会话+消息；不带 conversation_id 的 ask 维持 J1 行为（回归）
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from tests.ingest_helpers import upload_and_wait_response


# ---- 工具（与 test_multiturn 同款，前缀 test_j2%） ----


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
    return client.post(
        "/api/auth/login", json={"username": username, "password": "secret123"}
    ).json()["access_token"]


def _create_kb(client: TestClient, token: str, name: str) -> int:
    return client.post(
        "/api/kbs", json={"name": name}, headers=_auth(token)
    ).json()["id"]


def _upload(client: TestClient, kb_id: int, token: str, filename: str, content: bytes):
    """上传并等摄取收敛，返回**终态响应**（K4：上传接口只回 202 pending）。"""
    return upload_and_wait_response(client, kb_id, token, filename, content)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_conversation(client: TestClient, kb_id: int, token: str, title: str | None = None) -> dict:
    body = {} if title is None else {"title": title}
    resp = client.post(f"/api/kbs/{kb_id}/conversations", json=body, headers=_auth(token))
    assert resp.status_code == 201
    return resp.json()


@pytest.fixture(scope="module")
def client() -> TestClient:
    if not _pg_reachable():
        pytest.skip("PostgreSQL 不可达，跳过 J2 会话 API 测试")
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
    cur.execute("DELETE FROM knowledge_bases WHERE name LIKE 'test_j2%'")
    cur.execute("DELETE FROM users WHERE username LIKE 'test_j2%'")
    conn.close()


# ================= CRUD =================


def test_conversation_crud(client: TestClient) -> None:
    token = _register_and_login(client, "test_j2_crud")
    kb_id = _create_kb(client, token, "test_j2_crud_kb")

    # 创建 201（带 title）
    conv = _create_conversation(client, kb_id, token, title="第一轮")
    assert conv["title"] == "第一轮"
    assert conv["kb_id"] == kb_id

    # 列表含它
    lst = client.get(f"/api/kbs/{kb_id}/conversations", headers=_auth(token)).json()
    assert [c["id"] for c in lst] == [conv["id"]]

    # 详情空消息
    detail = client.get(f"/api/kbs/{kb_id}/conversations/{conv['id']}", headers=_auth(token)).json()
    assert detail["messages"] == []

    # 改名生效
    patched = client.patch(
        f"/api/kbs/{kb_id}/conversations/{conv['id']}",
        json={"title": "改名后"},
        headers=_auth(token),
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "改名后"

    # 删除 200 且再查 404
    assert client.delete(
        f"/api/kbs/{kb_id}/conversations/{conv['id']}", headers=_auth(token)
    ).status_code == 200
    assert client.get(
        f"/api/kbs/{kb_id}/conversations/{conv['id']}", headers=_auth(token)
    ).status_code == 404


def test_conversation_default_title(client: TestClient) -> None:
    token = _register_and_login(client, "test_j2_deftitle")
    kb_id = _create_kb(client, token, "test_j2_deftitle_kb")
    conv = _create_conversation(client, kb_id, token)
    assert conv["title"] == "新对话"


# ================= 归属校验 =================


def test_conversation_other_users_403(client: TestClient) -> None:
    token_a = _register_and_login(client, "test_j2_own_a")
    token_b = _register_and_login(client, "test_j2_own_b")
    kb_id = _create_kb(client, token_a, "test_j2_own_kb")
    # A 把 B 加成 viewer（B 能访问 KB，但会话仍私有）
    resp = client.post(
        f"/api/kbs/{kb_id}/members",
        json={"username": "test_j2_own_b", "role": "viewer"},
        headers=_auth(token_a),
    )
    assert resp.status_code == 201
    conv = _create_conversation(client, kb_id, token_a)

    # B 访问 A 的会话 → 403（详情/改名/删除）
    assert client.get(
        f"/api/kbs/{kb_id}/conversations/{conv['id']}", headers=_auth(token_b)
    ).status_code == 403
    assert client.patch(
        f"/api/kbs/{kb_id}/conversations/{conv['id']}",
        json={"title": "x"},
        headers=_auth(token_b),
    ).status_code == 403
    assert client.delete(
        f"/api/kbs/{kb_id}/conversations/{conv['id']}", headers=_auth(token_b)
    ).status_code == 403

    # B 用 A 的会话问 → 403
    resp = client.post(
        f"/api/kbs/{kb_id}/ask",
        json={"query": "差旅报销需要什么手续？", "conversation_id": conv["id"]},
        headers=_auth(token_b),
    )
    assert resp.status_code == 403

    # B 的会话列表只含自己的（空）
    assert client.get(f"/api/kbs/{kb_id}/conversations", headers=_auth(token_b)).json() == []


def test_conversation_wrong_kb_404(client: TestClient) -> None:
    token = _register_and_login(client, "test_j2_kb404")
    kb1 = _create_kb(client, token, "test_j2_kb404_1")
    kb2 = _create_kb(client, token, "test_j2_kb404_2")
    conv = _create_conversation(client, kb1, token)
    # 用 kb2 访问 kb1 的会话 → 404
    assert client.get(
        f"/api/kbs/{kb2}/conversations/{conv['id']}", headers=_auth(token)
    ).status_code == 404


def test_conversation_requires_auth_401(client: TestClient) -> None:
    token = _register_and_login(client, "test_j2_401")
    kb_id = _create_kb(client, token, "test_j2_401_kb")
    assert client.get(f"/api/kbs/{kb_id}/conversations").status_code == 401


def test_ask_unknown_conversation_404(client: TestClient) -> None:
    token = _register_and_login(client, "test_j2_unkconv")
    kb_id = _create_kb(client, token, "test_j2_unkconv_kb")
    resp = client.post(
        f"/api/kbs/{kb_id}/ask",
        json={"query": "x", "conversation_id": 999999},
        headers=_auth(token),
    )
    assert resp.status_code == 404


# ================= ask 落库 + 服务端历史 =================

POLICY_TEXT = (
    "出差住宿标准：一线城市每晚不超过六百元，其他城市每晚不超过四百元。"
    "交通费用凭票据实报销。年假按入职年限计算：满一年五天，满三年八天。"
).encode("utf-8")


def test_ask_with_conversation_persists_messages(client: TestClient) -> None:
    token = _register_and_login(client, "test_j2_ask")
    kb_id = _create_kb(client, token, "test_j2_ask_kb")
    _upload(client, kb_id, token, "policy.txt", POLICY_TEXT)
    conv = _create_conversation(client, kb_id, token)

    resp = client.post(
        f"/api/kbs/{kb_id}/ask",
        json={"query": "差旅报销需要什么手续？", "conversation_id": conv["id"]},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["conversation_id"] == conv["id"]

    detail = client.get(f"/api/kbs/{kb_id}/conversations/{conv['id']}", headers=_auth(token)).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][0]["content"] == "差旅报销需要什么手续？"
    asst = detail["messages"][1]
    assert asst["content"] == data["answer"]
    # 首轮无历史 → 改写 = 原句，sources 落库
    assert asst["rewritten_query"] == "差旅报销需要什么手续？"
    assert len(asst["sources"]) > 0
    assert {"filename", "chunk_index", "content", "score"} <= set(asst["sources"][0])


def test_ask_second_turn_uses_server_history(client: TestClient) -> None:
    token = _register_and_login(client, "test_j2_turn2")
    kb_id = _create_kb(client, token, "test_j2_turn2_kb")
    _upload(client, kb_id, token, "policy.txt", POLICY_TEXT)
    conv = _create_conversation(client, kb_id, token)

    client.post(
        f"/api/kbs/{kb_id}/ask",
        json={"query": "差旅报销需要什么手续？", "conversation_id": conv["id"]},
        headers=_auth(token),
    )
    # 第二轮短问句 → 服务端从库内历史改写
    resp = client.post(
        f"/api/kbs/{kb_id}/ask",
        json={"query": "那住宿标准呢？", "conversation_id": conv["id"]},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert resp.json()["rewritten_query"] == "差旅报销需要什么手续？ · 那住宿标准呢？"

    detail = client.get(f"/api/kbs/{kb_id}/conversations/{conv['id']}", headers=_auth(token)).json()
    # 4 条消息按 id 正序；assistant 第二轮 rewritten_query 落库
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant", "user", "assistant"]
    assert detail["messages"][3]["rewritten_query"] == "差旅报销需要什么手续？ · 那住宿标准呢？"


def test_ask_stream_with_conversation_persists(client: TestClient) -> None:
    token = _register_and_login(client, "test_j2_stream")
    kb_id = _create_kb(client, token, "test_j2_stream_kb")
    _upload(client, kb_id, token, "policy.txt", POLICY_TEXT)
    conv = _create_conversation(client, kb_id, token)

    resp = client.post(
        f"/api/kbs/{kb_id}/ask/stream",
        json={"query": "年假有几天？", "conversation_id": conv["id"]},
        headers=_auth(token),
    )
    assert resp.status_code == 200

    conv_id_in_event = None
    for line in resp.text.strip().split("\n\n"):
        if not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str == "[DONE]":
            continue
        payload = json.loads(data_str)
        if payload.get("type") == "sources":
            conv_id_in_event = payload.get("conversation_id")
    assert conv_id_in_event == conv["id"]

    detail = client.get(f"/api/kbs/{kb_id}/conversations/{conv['id']}", headers=_auth(token)).json()
    assert len(detail["messages"]) == 2


# ================= 标题 / 顺序 =================


def test_conversation_title_from_first_query(client: TestClient) -> None:
    token = _register_and_login(client, "test_j2_title")
    kb_id = _create_kb(client, token, "test_j2_title_kb")
    _upload(client, kb_id, token, "policy.txt", POLICY_TEXT)
    conv = _create_conversation(client, kb_id, token)
    assert conv["title"] == "新对话"

    q = "差旅报销需要什么手续？"
    client.post(
        f"/api/kbs/{kb_id}/ask",
        json={"query": q, "conversation_id": conv["id"]},
        headers=_auth(token),
    )
    detail = client.get(f"/api/kbs/{kb_id}/conversations/{conv['id']}", headers=_auth(token)).json()
    assert detail["title"] == q[:30]


def test_conversation_list_ordered_by_updated_at(client: TestClient) -> None:
    token = _register_and_login(client, "test_j2_order")
    kb_id = _create_kb(client, token, "test_j2_order_kb")
    conv_a = _create_conversation(client, kb_id, token, title="A")
    time.sleep(0.02)
    conv_b = _create_conversation(client, kb_id, token, title="B")

    # 初始 B 后建 → B 在前
    lst = client.get(f"/api/kbs/{kb_id}/conversations", headers=_auth(token)).json()
    assert [c["id"] for c in lst] == [conv_b["id"], conv_a["id"]]

    # 在 A 中发一条消息 → A 置顶
    client.post(
        f"/api/kbs/{kb_id}/ask",
        json={"query": "年假有几天？", "conversation_id": conv_a["id"]},
        headers=_auth(token),
    )
    lst = client.get(f"/api/kbs/{kb_id}/conversations", headers=_auth(token)).json()
    assert [c["id"] for c in lst] == [conv_a["id"], conv_b["id"]]


# ================= 级联删除 / 回归 =================


def test_delete_conversation_cascades_messages(client: TestClient) -> None:
    token = _register_and_login(client, "test_j2_cascade")
    kb_id = _create_kb(client, token, "test_j2_cascade_kb")
    conv = _create_conversation(client, kb_id, token)

    for _ in range(2):
        client.post(
            f"/api/kbs/{kb_id}/ask",
            json={"query": "年假有几天？", "conversation_id": conv["id"]},
            headers=_auth(token),
        )
    detail = client.get(f"/api/kbs/{kb_id}/conversations/{conv['id']}", headers=_auth(token)).json()
    assert len(detail["messages"]) == 4

    assert client.delete(
        f"/api/kbs/{kb_id}/conversations/{conv['id']}", headers=_auth(token)
    ).status_code == 200
    assert client.get(
        f"/api/kbs/{kb_id}/conversations/{conv['id']}", headers=_auth(token)
    ).status_code == 404

    # DB 层：会话与消息级联删除
    import psycopg2

    s = Settings()
    conn = psycopg2.connect(
        host=s.pg_host, port=s.pg_port, user=s.pg_user,
        password=s.pg_password, dbname=s.pg_database,
    )
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM conversations WHERE id = %s", (conv["id"],))
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT count(*) FROM chat_messages WHERE conversation_id = %s", (conv["id"],))
    assert cur.fetchone()[0] == 0
    conn.close()


def test_ask_without_conversation_keeps_j1_behavior(client: TestClient) -> None:
    """不带 conversation_id 的 ask 维持 J1 行为（前端传 history），且不建会话。"""
    token = _register_and_login(client, "test_j2_plain")
    kb_id = _create_kb(client, token, "test_j2_plain_kb")
    _upload(client, kb_id, token, "policy.txt", POLICY_TEXT)

    resp = client.post(
        f"/api/kbs/{kb_id}/ask",
        json={
            "query": "那住宿标准呢？",
            "history": [{"role": "user", "content": "差旅报销需要什么手续？"}],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["conversation_id"] is None
    assert data["rewritten_query"] == "差旅报销需要什么手续？ · 那住宿标准呢？"

    # 未创建任何会话
    assert client.get(f"/api/kbs/{kb_id}/conversations", headers=_auth(token)).json() == []
