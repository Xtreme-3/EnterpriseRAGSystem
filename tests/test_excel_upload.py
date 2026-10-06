"""K10 Excel 上传链路测试 —— 全部**离线可跑**（DATA_DIR → tmp_path，mock 供应商）。

真实 openpyxl 生成的 .xlsx 走完整摄取链路（上传 → 解析 → 切片 → 索引），
外加 `/api/config/upload` 的注册表下发契约。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.main import app
from tests.ingest_helpers import auth, upload_and_wait


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _make_xlsx(path, sheets: dict[str, list[list]]) -> None:
    wb = Workbook()
    first = True
    for title, rows in sheets.items():
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        for row in rows:
            ws.append(row)
        first = False
    wb.save(path)


def test_upload_config_lists_registry(client):
    """accept 列表以后端注册表为准（KbsList/DocList 的硬编码副本已删除）。"""
    token = _register_and_login(client, "test_k10_cfg")
    resp = client.get("/api/config/upload", headers=_auth(token))
    assert resp.status_code == 200
    exts = resp.json()["supported_exts"]
    assert ".xlsx" in exts and ".pdf" in exts and ".md" in exts


def test_upload_xlsx_ingests_end_to_end(client, tmp_path):
    token = _register_and_login(client, "test_k10_up")
    kb_id = client.post(
        "/api/kbs", json={"name": "test_k10_kb"}, headers=_auth(token)
    ).json()["id"]

    xlsx = tmp_path / "standards.xlsx"
    _make_xlsx(xlsx, sheets={
        "差旅标准": [["城市", "住宿上限"], ["一线城市", 600], ["二线城市", 450]],
    })
    # K4：上传异步化，等终态再断言；顺带把原来没关的文件句柄改成 read_bytes()
    body = upload_and_wait(
        client,
        kb_id,
        token,
        "standards.xlsx",
        xlsx.read_bytes(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert body["status"] == "indexed"
    assert body["chunk_count"] >= 1


def test_upload_unsupported_ext_still_rejected(client):
    """注册表下发后，真正不支持的扩展名照旧 400。"""
    token = _register_and_login(client, "test_k10_doc")
    kb_id = client.post(
        "/api/kbs", json={"name": "test_k10_doc_kb"}, headers=_auth(token)
    ).json()["id"]
    resp = client.post(
        f"/api/kbs/{kb_id}/documents",
        files={"file": ("legacy.doc", b"old format", "application/msword")},
        headers=_auth(token),
    )
    assert resp.status_code == 400


# ---- helpers ----

def _register_and_login(client: TestClient, username: str) -> str:
    client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    return client.post(
        "/api/auth/login", json={"username": username, "password": "secret123"}
    ).json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
