"""L2：审计日志测试 —— 全部离线（DATA_DIR → tmp_path，mock 供应商）。

两条核心设计决策各有直接验证用例（见需求卡「关键设计决策」）：
- 决策①独立短会话：401 登录失败也落库（挂在请求会话上必被 get_db 的 rollback 吞掉）；
  best-effort：审计炸了业务照常 202
- 决策②不设 FK：删库后该库的审计记录幸存

数据全部走真实 API 流程触发（非直插 DB），保证测的是挂钩本身。
"""
from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import audit
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


def _promote_admin(tmp_path: Path, username: str) -> None:
    """把用户全局角色改为 admin（SQLite 直改，模拟 I2 的「后续管理接口授予」）。"""
    conn = sqlite3.connect(str(tmp_path / "data" / "rag.db"))
    conn.execute("UPDATE users SET role='admin' WHERE username=?", (username,))
    conn.commit()
    conn.close()


def _list_audit(client: TestClient, token: str, **params: object) -> dict:
    r = client.get("/api/audit-logs", params=params, headers=_auth(token))
    assert r.status_code == 200, r.text
    return r.json()


def _actions_of(page: dict) -> list[str]:
    return [item["action"] for item in page["items"]]


# ---- 401 失败也落库（决策①）----

def test_login_failed_audited_wrong_password_and_unknown_user(client, tmp_path) -> None:
    admin = _register_and_login(client, "test_l2_adm")
    _promote_admin(tmp_path, "test_l2_adm")
    client.post("/api/auth/register", json={"username": "test_l2_u", "password": "secret123"})

    # 已知用户密码错：actor_id 有值（人查得到是谁在试）
    r = client.post("/api/auth/login", json={"username": "test_l2_u", "password": "wrong!"})
    assert r.status_code == 401
    # 不存在的用户名（撞库探测）：actor_id=NULL，actor_name 恒记尝试名
    r = client.post("/api/auth/login", json={"username": "ghost_user", "password": "x"})
    assert r.status_code == 401

    rows = _list_audit(client, admin, action="auth.login_failed")["items"]
    assert len(rows) == 2
    known = next(i for i in rows if i["actor_name"] == "test_l2_u")
    ghost = next(i for i in rows if i["actor_name"] == "ghost_user")
    assert known["actor_id"] is not None and known["detail"] == "reason=密码错误"
    assert ghost["actor_id"] is None and ghost["detail"] == "reason=用户不存在"
    # 安全红线：任何字段都不落密码
    assert "wrong!" not in str(rows) and "secret123" not in str(rows)


def test_register_and_login_success_audited(client, tmp_path) -> None:
    admin = _register_and_login(client, "test_l2_adm2")
    _promote_admin(tmp_path, "test_l2_adm2")
    page = _list_audit(client, admin)
    actions = _actions_of(page)
    # 注册与登录各一条；注册的 target 带用户标识
    assert "auth.register" in actions and "auth.login" in actions
    reg = next(i for i in page["items"] if i["action"] == "auth.register")
    assert reg["target"] == f"user:{reg['actor_id']}(test_l2_adm2)"


# ---- KB 增删（决策②）----

def test_kb_create_delete_audited_and_survives_deletion(client, tmp_path) -> None:
    admin = _register_and_login(client, "test_l2_adm3")
    _promote_admin(tmp_path, "test_l2_adm3")
    kb_id = client.post("/api/kbs", json={"name": "l2临时库"}, headers=_auth(admin)).json()["id"]
    assert client.delete(f"/api/kbs/{kb_id}", headers=_auth(admin)).status_code == 200

    # 删库之后按 kb_id 过滤仍查得到：create 与 delete 两条都在（no-FK 的直接验证）
    page = _list_audit(client, admin, kb_id=kb_id)
    actions = _actions_of(page)
    assert "kb.create" in actions and "kb.delete" in actions
    deleted = next(i for i in page["items"] if i["action"] == "kb.delete")
    assert deleted["target"] == f"kb:{kb_id}(l2临时库)"
    assert deleted["kb_id"] == kb_id


# ---- 文档上传 / 删除 / 重试 ----

def test_document_upload_and_delete_audited(client, tmp_path) -> None:
    token = _register_and_login(client, "test_l2_doc")
    kb_id = client.post("/api/kbs", json={"name": "l2doc库"}, headers=_auth(token)).json()["id"]

    r = client.post(
        f"/api/kbs/{kb_id}/documents",
        files={"file": ("体检报告.txt", io.BytesIO("审计内容".encode()), "text/plain")},
        headers=_auth(token),
    )
    assert r.status_code == 202
    doc_id = r.json()["id"]
    assert client.delete(f"/api/documents/{doc_id}", headers=_auth(token)).status_code == 200

    conn = sqlite3.connect(str(tmp_path / "data" / "rag.db"))
    rows = conn.execute(
        "SELECT action, target FROM audit_logs WHERE kb_id=? ORDER BY id", (kb_id,)
    ).fetchall()
    conn.close()
    actions = [a for a, _ in rows]
    assert "document.upload" in actions and "document.delete" in actions
    # 受理即记（不等摄取终态）+ 删前记（文件名只有此刻拿得到）
    for action, target in rows:
        if action in ("document.upload", "document.delete"):
            assert target == f"document:{doc_id}(体检报告.txt)"


def test_document_retry_audited(client, tmp_path) -> None:
    token = _register_and_login(client, "test_l2_retry")
    _promote_admin(tmp_path, "test_l2_retry")
    kb_id = client.post("/api/kbs", json={"name": "l2retry库"}, headers=_auth(token)).json()["id"]

    # 种一份 failed 文档 + 原件（同步摄取路径不存原件，重试要求原件在）
    conn = sqlite3.connect(str(tmp_path / "data" / "rag.db"))
    cur = conn.execute(
        "INSERT INTO documents (kb_id, filename, file_type, status, chunk_count, created_at)"
        " VALUES (?, 'failed.txt', 'txt', 'failed', 0, datetime('now'))",
        (kb_id,),
    )
    doc_id = cur.lastrowid
    conn.commit()
    conn.close()
    uploads = tmp_path / "data" / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / f"{doc_id}.txt").write_text("重试用原件", encoding="utf-8")

    r = client.post(f"/api/documents/{doc_id}/retry", headers=_auth(token))
    assert r.status_code == 202
    rows = _list_audit(client, token, action="document.retry")
    assert rows["total"] == 1
    assert rows["items"][0]["target"] == f"document:{doc_id}(failed.txt)"


# ---- 成员增删改 ----

def test_member_add_role_change_remove_audited(client, tmp_path) -> None:
    owner = _register_and_login(client, "test_l2_own")
    member_name = "test_l2_mem"
    member_token = _register_and_login(client, member_name)
    member_id = client.get("/api/auth/me", headers=_auth(member_token)).json()["id"]
    kb_id = client.post("/api/kbs", json={"name": "l2成员库"}, headers=_auth(owner)).json()["id"]

    assert (
        client.post(
            f"/api/kbs/{kb_id}/members",
            json={"username": member_name, "role": "editor"},
            headers=_auth(owner),
        ).status_code
        == 201
    )
    # 改角色（editor → viewer）
    assert (
        client.patch(
            f"/api/kbs/{kb_id}/members/{member_id}",
            json={"role": "viewer"},
            headers=_auth(owner),
        ).status_code
        == 200
    )
    assert client.delete(f"/api/kbs/{kb_id}/members/{member_id}", headers=_auth(owner)).status_code == 200

    # owner 本人不是 admin（查询接口的权限由专门用例验证），从 SQLite 直读挂钩产物
    conn = sqlite3.connect(str(tmp_path / "data" / "rag.db"))
    got = conn.execute(
        "SELECT action, target, detail FROM audit_logs WHERE kb_id=? ORDER BY id", (kb_id,)
    ).fetchall()
    conn.close()
    actions = {a: (t, d) for a, t, d in got}
    assert actions["member.add"][1] == "role=editor"
    assert actions["member.role_change"][1] == "role=editor→viewer"
    assert "member.remove" in actions
    # member.target 记的是**被操作者**，不是操作者
    assert f"({member_name})" in actions["member.add"][0]


# ---- 查询接口：admin 门槛 + 过滤 + 分页 ----

def test_audit_query_admin_only(client, tmp_path) -> None:
    _register_and_login(client, "test_l2_plain")
    token = _register_and_login(client, "test_l2_admin")
    _promote_admin(tmp_path, "test_l2_admin")

    # 未登录 → 401；非 admin → 403；admin → 200
    assert client.get("/api/audit-logs").status_code == 401
    plain = client.post(
        "/api/auth/login", json={"username": "test_l2_plain", "password": "secret123"}
    ).json()["access_token"]
    assert client.get("/api/audit-logs", headers=_auth(plain)).status_code == 403
    assert client.get("/api/audit-logs", headers=_auth(token)).status_code == 200


def test_audit_filters_and_pagination(client, tmp_path) -> None:
    token = _register_and_login(client, "test_l2_pg_admin")
    _promote_admin(tmp_path, "test_l2_pg_admin")
    # 再造两个用户 → register 3 条
    for name in ("test_l2_pg_a", "test_l2_pg_b"):
        client.post("/api/auth/register", json={"username": name, "password": "secret123"})

    reg_page = _list_audit(client, token, action="auth.register")
    assert reg_page["total"] == 3
    assert {i["actor_name"] for i in reg_page["items"]} >= {"test_l2_pg_a", "test_l2_pg_b"}

    # 分页：limit=1 / offset=1 拿到第二条，total 不变
    paged = _list_audit(client, token, action="auth.register", limit=1, offset=1)
    assert paged["total"] == 3 and len(paged["items"]) == 1
    all_items = reg_page["items"]
    assert paged["items"][0]["id"] == all_items[1]["id"]  # created_at desc 的一致性

    # 无关过滤值：不炸、total=0
    empty = _list_audit(client, token, action="no.such.action")
    assert empty["total"] == 0 and empty["items"] == []


# ---- best-effort：失败路径的审计挂了，登录失败响应不受影响 ----

def test_detached_audit_failure_does_not_break_login(client, tmp_path, monkeypatch) -> None:
    _register_and_login(client, "test_l2_boom")

    def _boom(**kwargs):
        raise RuntimeError("审计存储炸了")

    # 只打失败路径的构造（成功路径的挂钩此用例不触发）
    monkeypatch.setattr(audit, "AuditLog", _boom)
    r = client.post("/api/auth/login", json={"username": "test_l2_boom", "password": "bad"})
    # 审计构造抛错被 record_audit_detached 吞掉：登录失败仍是 401，不 500
    assert r.status_code == 401
