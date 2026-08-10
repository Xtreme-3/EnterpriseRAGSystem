"""B1：FastAPI 骨架测试（/health、/、统一异常 JSON）。

用 TestClient 但不进入上下文管理器，跳过 lifespan（不连数据库），保持离线可跑；
真实启动由 `uvicorn app.main:app` 时执行（见 B1 验收，手动验证）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    # raise_server_exceptions=False：统一异常处理器返回的 500 应作为响应被断言，
    # 而不是让 TestClient 直接把服务端异常重新抛出（Starlette 默认行为）。
    return TestClient(app, raise_server_exceptions=False)


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_root_info(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"]
    assert body["version"]
    assert body["docs"] == "/docs"
    assert body["health"] == "/health"


def test_unhandled_exception_returns_json(client: TestClient) -> None:
    """未处理异常 → 统一 JSON 500，不泄露堆栈与异常原文。"""

    @app.get("/_boom")
    def _boom() -> None:
        raise RuntimeError("内部测试异常，不应出现在响应里")

    try:
        resp = client.get("/_boom")
    finally:
        # 移除临时路由，避免污染后续测试
        app.router.routes = [
            r for r in app.router.routes if getattr(r, "path", None) != "/_boom"
        ]

    assert resp.status_code == 500
    body = resp.json()
    assert body == {"detail": "服务器内部错误"}
    assert "内部测试异常" not in resp.text
