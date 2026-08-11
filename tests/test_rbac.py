"""I2：RBAC 权限测试 —— 角色矩阵 + 成员管理 + admin 旁路。

依赖 PostgreSQL（复用 test_inspect 模式，PG 不可达自动跳过）。
"""
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
    return client.post(
        "/api/auth/login", json={"username": username, "password": "secret123"}
    ).json()["access_token"]


def _create_kb(client: TestClient, token: str, name: str) -> int:
    return client.post(
        "/api/kbs", json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    ).json()["id"]


def _upload(client: TestClient, kb_id: int, token: str, filename: str, content: bytes):
    return client.post(
        f"/api/kbs/{kb_id}/documents",
        files={"file": (filename, io.BytesIO(content), "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )


def _add_member(client: TestClient, kb_id: int, token: str, username: str, role: str):
    return client.post(
        f"/api/kbs/{kb_id}/members",
        json={"username": username, "role": role},
        headers={"Authorization": f"Bearer {token}"},
    )


def _promote_admin(username: str) -> None:
    """把用户全局角色改为 admin（模拟后续管理接口授予）。"""
    import psycopg2
    s = Settings()
    conn = psycopg2.connect(
        host=s.pg_host, port=s.pg_port, user=s.pg_user,
        password=s.pg_password, dbname=s.pg_database,
    )
    conn.autocommit = True
    conn.cursor().execute("UPDATE users SET role='admin' WHERE username=%s", (username,))
    conn.close()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _setup_team(client: TestClient, suffix: str) -> dict:
    """创建 owner/viewer/editor/非成员 四用户 + 一个 KB，owner 添加 viewer 与 editor 为成员。"""
    team = {
        "owner": _register_and_login(client, f"test_i2_{suffix}_o"),
        "viewer": _register_and_login(client, f"test_i2_{suffix}_v"),
        "editor": _register_and_login(client, f"test_i2_{suffix}_e"),
        "outsider": _register_and_login(client, f"test_i2_{suffix}_x"),
    }
    team["kb"] = _create_kb(client, team["owner"], f"test_i2_{suffix}_kb")
    for u, role in ((f"test_i2_{suffix}_v", "viewer"), (f"test_i2_{suffix}_e", "editor")):
        resp = _add_member(client, team["kb"], team["owner"], u, role)
        assert resp.status_code == 201
    return team


@pytest.fixture(scope="module")
def client() -> TestClient:
    if not _pg_reachable():
        pytest.skip("PostgreSQL 不可达，跳过 I2 RBAC 测试")
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
    cur.execute("DELETE FROM knowledge_bases WHERE name LIKE 'test_i2%'")
    cur.execute("DELETE FROM users WHERE username LIKE 'test_i2%'")
    conn.close()


# ---- 全局角色 ----

def test_register_and_me_include_role(client: TestClient) -> None:
    resp = client.post("/api/auth/register", json={"username": "test_i2_role", "password": "secret123"})
    assert resp.status_code == 201
    assert resp.json()["role"] == "user"
    token = client.post("/api/auth/login", json={"username": "test_i2_role", "password": "secret123"}).json()["access_token"]
    me = client.get("/api/auth/me", headers=_auth(token)).json()
    assert me["role"] == "user"


# ---- 角色矩阵 ----

def test_list_members_owner_first_with_roles(client: TestClient) -> None:
    team = _setup_team(client, "list")
    members = client.get(f"/api/kbs/{team['kb']}/members", headers=_auth(team["owner"])).json()
    roles = {m["username"]: m["role"] for m in members}
    assert roles["test_i2_list_o"] == "owner"
    assert roles["test_i2_list_v"] == "viewer"
    assert roles["test_i2_list_e"] == "editor"
    assert members[0]["role"] == "owner"  # owner 在前


def test_list_kbs_role_per_member(client: TestClient) -> None:
    team = _setup_team(client, "vis")
    roles = {}
    for key in ("owner", "viewer", "editor"):
        kbs = client.get("/api/kbs", headers=_auth(team[key])).json()
        roles[key] = kbs[0]["role"]
    assert roles["owner"] == "owner"
    assert roles["viewer"] == "viewer"
    assert roles["editor"] == "editor"
    # 非成员看不到该库
    outsider_ids = {kb["id"] for kb in client.get("/api/kbs", headers=_auth(team["outsider"])).json()}
    assert team["kb"] not in outsider_ids


def test_get_kb_detail_role_field(client: TestClient) -> None:
    team = _setup_team(client, "role")
    detail = client.get(f"/api/kbs/{team['kb']}", headers=_auth(team["viewer"])).json()
    assert detail["role"] == "viewer"


def test_viewer_can_read_not_write(client: TestClient) -> None:
    team = _setup_team(client, "vwr")
    kb_id = team["kb"]
    viewer = team["viewer"]
    # 读可以
    assert client.get(f"/api/kbs/{kb_id}", headers=_auth(viewer)).status_code == 200
    assert client.get(f"/api/kbs/{kb_id}/documents", headers=_auth(viewer)).status_code == 200
    # 写全部 403：上传文档 / 成员列表 / 加成员 / 删库
    assert _upload(client, kb_id, viewer, "x.txt", "内容".encode("utf-8")).status_code == 403
    assert client.get(f"/api/kbs/{kb_id}/members", headers=_auth(viewer)).status_code == 403
    assert _add_member(client, kb_id, viewer, "test_i2_vwr_x", "viewer").status_code == 403
    assert client.delete(f"/api/kbs/{kb_id}", headers=_auth(viewer)).status_code == 403


def test_editor_can_manage_docs_not_kb(client: TestClient) -> None:
    team = _setup_team(client, "edt")
    kb_id = team["kb"]
    editor = team["editor"]
    viewer = team["viewer"]
    # editor 上传 + 删除文档
    up = _upload(client, kb_id, editor, "policy.txt", "人工智能是计算机科学的一个分支。".encode("utf-8"))
    assert up.status_code == 201
    doc_id = up.json()["id"]
    assert client.delete(f"/api/documents/{doc_id}", headers=_auth(editor)).status_code == 200
    # viewer 上传仍 403
    assert _upload(client, kb_id, viewer, "y.txt", "x".encode("utf-8")).status_code == 403
    # editor 可看成员，但不可加成员/删库
    assert client.get(f"/api/kbs/{kb_id}/members", headers=_auth(editor)).status_code == 200
    assert _add_member(client, kb_id, editor, "test_i2_edt_x", "viewer").status_code == 403
    assert client.delete(f"/api/kbs/{kb_id}", headers=_auth(editor)).status_code == 403


def test_nonmember_denied(client: TestClient) -> None:
    team = _setup_team(client, "nm")
    outsider = team["outsider"]
    assert client.get(f"/api/kbs/{team['kb']}", headers=_auth(outsider)).status_code == 403
    assert client.get(f"/api/kbs/{team['kb']}/members", headers=_auth(outsider)).status_code == 403
    assert _upload(client, team["kb"], outsider, "z.txt", "x".encode("utf-8")).status_code == 403


# ---- 成员管理 ----

def test_add_member_validation(client: TestClient) -> None:
    team = _setup_team(client, "mgmt")
    kb_id = team["kb"]
    auth_h = _auth(team["owner"])
    # 用户不存在 404
    assert _add_member(client, kb_id, team["owner"], "test_i2_nobody", "viewer").status_code == 404
    # 重复成员 409
    assert _add_member(client, kb_id, team["owner"], "test_i2_mgmt_v", "editor").status_code == 409
    # 添加 owner 409
    assert _add_member(client, kb_id, team["owner"], "test_i2_mgmt_o", "editor").status_code == 409
    # 非法 role 422
    assert _add_member(client, kb_id, team["owner"], "test_i2_mgmt_v", "admin").status_code == 422


def test_owner_row_immutable(client: TestClient) -> None:
    team = _setup_team(client, "own")
    kb_id = team["kb"]
    members = client.get(f"/api/kbs/{kb_id}/members", headers=_auth(team["owner"])).json()
    owner_id = next(m["user_id"] for m in members if m["role"] == "owner")
    assert client.patch(
        f"/api/kbs/{kb_id}/members/{owner_id}", json={"role": "viewer"}, headers=_auth(team["owner"])
    ).status_code == 400
    assert client.delete(
        f"/api/kbs/{kb_id}/members/{owner_id}", headers=_auth(team["owner"])
    ).status_code == 400


def test_promote_viewer_to_editor_unlocks_write(client: TestClient) -> None:
    team = _setup_team(client, "promo")
    kb_id = team["kb"]
    members = client.get(f"/api/kbs/{kb_id}/members", headers=_auth(team["owner"])).json()
    viewer_id = next(m["user_id"] for m in members if m["role"] == "viewer")
    resp = client.patch(
        f"/api/kbs/{kb_id}/members/{viewer_id}", json={"role": "editor"}, headers=_auth(team["owner"])
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "editor"
    # viewer（已升 editor）现在可上传
    up = _upload(client, kb_id, team["viewer"], "promo.txt", "内容".encode("utf-8"))
    assert up.status_code == 201


def test_remove_member_revokes_access(client: TestClient) -> None:
    team = _setup_team(client, "rm")
    kb_id = team["kb"]
    members = client.get(f"/api/kbs/{kb_id}/members", headers=_auth(team["owner"])).json()
    viewer_id = next(m["user_id"] for m in members if m["role"] == "viewer")
    resp = client.delete(f"/api/kbs/{kb_id}/members/{viewer_id}", headers=_auth(team["owner"]))
    assert resp.status_code == 200
    assert resp.json()["status"] == "removed"
    # 移除后失去访问
    assert client.get(f"/api/kbs/{kb_id}", headers=_auth(team["viewer"])).status_code == 403
    # 重复删除 404
    assert client.delete(f"/api/kbs/{kb_id}/members/{viewer_id}", headers=_auth(team["owner"])).status_code == 404


# ---- admin 全局旁路 ----

def test_admin_acts_as_owner_any_kb(client: TestClient) -> None:
    owner = _register_and_login(client, "test_i2_adm_o")
    kb_id = _create_kb(client, owner, "test_i2_adm_kb")
    admin = _register_and_login(client, "test_i2_adm_a")
    _promote_admin("test_i2_adm_a")
    # admin 可见全部库，role=owner
    kbs = client.get("/api/kbs", headers=_auth(admin)).json()
    assert any(k["id"] == kb_id and k["role"] == "owner" for k in kbs)
    # admin 可删他人库
    assert client.delete(f"/api/kbs/{kb_id}", headers=_auth(admin)).status_code == 200
    assert client.get(f"/api/kbs/{kb_id}", headers=_auth(owner)).status_code == 404


def test_admin_can_manage_others_members(client: TestClient) -> None:
    owner = _register_and_login(client, "test_i2_adm2_o")
    kb_id = _create_kb(client, owner, "test_i2_adm2_kb")
    admin = _register_and_login(client, "test_i2_adm2_a")
    _promote_admin("test_i2_adm2_a")
    _register_and_login(client, "test_i2_adm2_m")
    # admin 在他人库添加成员
    resp = _add_member(client, kb_id, admin, "test_i2_adm2_m", "editor")
    assert resp.status_code == 201
    assert resp.json()["role"] == "editor"
