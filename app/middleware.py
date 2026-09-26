"""request-id 中间件（K8-2）：让"客户端看到的报错"能对上"服务端日志"。

没有它的时候：用户截图「服务器内部错误」，运维翻日志只能靠时间戳猜是哪一次请求，
并发一高就完全对不上号。有了它：任意响应都带 ``X-Request-ID``（响应头 + 500 响应体），
把它贴进日志搜索能唯一定位整条链路。

### 为什么是纯 ASGI 中间件，而不是 ``BaseHTTPMiddleware``

``BaseHTTPMiddleware`` 的 ``call_next`` 在**响应体开始流出之前**就返回，中间件的
``finally`` 会早于 SSE token 生成执行 —— 一旦在那里重置 contextvar，流式问答期间
产生的日志（含 `chat.py` 的流式失败堆栈）就会丢掉 request_id，恰好是最需要它的时候。

纯 ASGI 版本把 ``await self.app(...)`` 整个包住；对 streaming 响应，
Starlette 会在这次调用内把响应体发完，contextvar 因此始终有效。
"""
from __future__ import annotations

import uuid

from app.logging_config import request_id_ctx

REQUEST_ID_HEADER = "X-Request-ID"
_HEADER_BYTES = REQUEST_ID_HEADER.lower().encode("latin-1")


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


class RequestIdMiddleware:
    """透传或生成 ``X-Request-ID``，注入日志上下文，并回写响应头。"""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        incoming = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in (scope.get("headers") or [])
        }
        request_id = (incoming.get(REQUEST_ID_HEADER.lower()) or "").strip() or new_request_id()

        token = request_id_ctx.set(request_id)
        scope["state"] = {**(scope.get("state") or {}), "request_id": request_id}

        async def send_with_request_id(message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    item for item in (message.get("headers") or []) if item[0].lower() != _HEADER_BYTES
                ]
                headers.append((_HEADER_BYTES, request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            request_id_ctx.reset(token)
