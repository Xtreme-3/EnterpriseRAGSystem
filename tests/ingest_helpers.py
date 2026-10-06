"""K4 测试辅助：等异步摄取收敛。

上传接口从「201 + 同步完成」变成「202 + 后台跑」之后，凡"上传 → 立刻断言
indexed"的用例都必须改成"上传 → 轮询至终态"。这个模块只做这一件事，避免
十几个测试文件各写一份轮询（写歪一份就变成偶发红）。
"""
from __future__ import annotations

import io
import time
from typing import Any

from fastapi.testclient import TestClient

#: 文档的终态。failed 也是终态 —— 等待函数只负责"等到不再变化"，
#: "该成功还是该失败"由调用方断言，这样正反两种用例共用同一个函数。
TERMINAL = ("indexed", "failed")


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def wait_document(
    client: TestClient, doc_id: int, token: str, timeout: float = 15.0
) -> dict[str, Any]:
    """轮询到文档离开非终态，返回最终 JSON；超时抛 AssertionError（附最后状态）。

    超时上限给得宽（15s）是因为真模型下后台任务可能确实要跑一会儿；
    mock 下通常 50ms 内就收敛，所以不会拖慢测试。
    """
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        resp = client.get(f"/api/documents/{doc_id}", headers=auth(token))
        if resp.status_code == 200:
            last = resp.json()
            if last["status"] in TERMINAL:
                return last
        time.sleep(0.02)
    raise AssertionError(f"文档 {doc_id} 在 {timeout}s 内未收敛为终态，最后状态：{last}")


def upload_raw(
    client: TestClient,
    kb_id: int,
    token: str,
    filename: str,
    content: bytes,
    content_type: str = "text/plain",
):
    """只发上传请求，不等待 —— 需要断言 202 / pending 的用例用它。"""
    return client.post(
        f"/api/kbs/{kb_id}/documents",
        files={"file": (filename, io.BytesIO(content), content_type)},
        headers=auth(token),
    )


def upload_and_wait(
    client: TestClient,
    kb_id: int,
    token: str,
    filename: str,
    content: bytes,
    content_type: str = "text/plain",
) -> dict[str, Any]:
    """上传并等到终态，返回最终文档 JSON。

    顺带把"上传必须返回 202"钉在这里：调用方不必各自再断言一次，
    万一有人把接口改回同步 201，这个 helper 会第一时间红。
    """
    resp = upload_raw(client, kb_id, token, filename, content, content_type)
    assert resp.status_code == 202, (
        f"上传应返回 202（K4 异步化），实际 {resp.status_code}: {resp.text[:200]}"
    )
    return wait_document(client, resp.json()["id"], token)


def upload_and_wait_response(
    client: TestClient,
    kb_id: int,
    token: str,
    filename: str,
    content: bytes,
    content_type: str = "text/plain",
):
    """上传并等到终态，返回**终态那次 GET 的响应**。

    刻意返回 GET 的响应而不是 POST 的：既有用例大量写成
    ``resp = _upload(...)`` 然后 ``resp.json()["status"] == "indexed"``，
    返回"终态响应"能让它们**一行不改**地继续成立（POST 现在只回 pending，
    直接把 POST 响应透出去会让所有这类断言变成假绿）。

    **非 202 的响应原样透传**：不少用例本来就靠上传拿错误码（403 越权 / 404 库不存在 /
    400 类型不支持 / 400 空文件），这些都在**请求期**就被拒了、根本没有后台任务可等。
    对它们断言 202 会把好用例判红。要"上传必须是 202"的严格契约断言用
    ``upload_and_wait``（它不带 response 版本，见上）。
    """
    up = upload_raw(client, kb_id, token, filename, content, content_type)
    if up.status_code != 202:
        return up
    doc_id = up.json()["id"]
    wait_document(client, doc_id, token)
    return client.get(f"/api/documents/{doc_id}", headers=auth(token))
