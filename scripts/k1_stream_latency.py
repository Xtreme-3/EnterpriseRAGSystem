"""K1 首字节延迟实测：证明「真流式」把首字节与模型首字延迟解耦。

用法（项目根目录，用项目 venv 跑；不依赖真实 API Key，全部离线）：

    ./.venv/Scripts/python.exe scripts/k1_stream_latency.py     # Windows
    ./.venv/bin/python scripts/k1_stream_latency.py             # macOS / Linux

做法：把 LLM 换成「首 token 前先阻塞 1.5 秒」的桩模型，模拟真模型的 TLS 握手 + 排队 + prefill。
对照两个口径：
- 改造前：``/ask/stream`` 先同步跑完 ``rag.ask()`` 才发第一个字节 → 首字节 ≈ 整段生成耗时
- 改造后：stage 事件在检索之前就下发 → 首字节与模型首字延迟无关

为什么不用 ``TestClient`` / ``httpx.ASGITransport``：
两者都会把整个响应体缓冲后才交给调用方，测出的「首字节」其实是「整段结束」，
无论流式与否都得到同一个数，完全看不出差别。本脚本直接在 ASGI 层记录每一帧
``http.response.body`` 的发送时刻 —— 那才是真正「第一个字节到达客户端」的时间点。

另一个坑：Starlette 的 ``StreamingResponse`` 会并发跑 ``listen_for_disconnect``，
**谁先结束谁取消对方**。手工驱动 http.client 时 ``receive()`` 第二次必须挂起等待，
若直接返回 ``http.disconnect``，刚启动的流会被立刻取消（表现为「状态码 200 但一个 body 帧都没有」）。
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Iterator

TMP = Path(tempfile.mkdtemp(prefix="k1_latency_"))
os.environ.update({
    "DATA_DIR": str(TMP / "data"),
    "VECTOR_STORE": "chroma",
    "RAG_PROVIDER": "mock",
    "EMBEDDING_PROVIDER": "mock",
    "LLM_PROVIDER": "mock",
    "CHUNK_SIZE": "200",
    "CHUNK_OVERLAP": "20",
    "TOP_K": "3",
})

import app.api.chat as chat_api  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.ingestion.pipeline import IngestionPipeline  # noqa: E402
from app.providers.base import LLMProvider  # noqa: E402
from app.providers.factory import build_embedding, build_reranker  # noqa: E402
from app.rag.pipeline import RagPipeline  # noqa: E402
from app.storage.db import init_db  # noqa: E402
from app.storage.vector_store import build_vector_store  # noqa: E402

FIRST_TOKEN_DELAY = 1.5   # 秒：模拟真模型 TLS 握手 + 排队 + prefill
TOKEN_INTERVAL = 0.2      # 秒：每个 token 的间隔
ANSWER_TOKENS = ["根据资料，", "一线城市", "每晚不超过", "六百元", "，其他城市四百元。"]
DOC_TEXT = "出差住宿标准：一线城市每晚不超过六百元，其他城市不超过四百元。"


class SlowLLM(LLMProvider):
    """首 token 前阻塞 FIRST_TOKEN_DELAY，之后每 TOKEN_INTERVAL 吐一个 token。"""

    def complete(
        self, prompt: str, *, max_tokens: int | None = None, system: str | None = None
    ) -> str:
        time.sleep(FIRST_TOKEN_DELAY)
        return "".join(ANSWER_TOKENS)

    def stream(
        self, prompt: str, *, max_tokens: int | None = None, system: str | None = None
    ) -> Iterator[str]:
        time.sleep(FIRST_TOKEN_DELAY)
        for tok in ANSWER_TOKENS:
            time.sleep(TOKEN_INTERVAL)
            yield tok


def build_slow_pipeline(_request=None) -> RagPipeline:
    s = get_settings()
    _, session_factory = init_db(s)
    return RagPipeline(
        settings=s,
        session_factory=session_factory,
        vector_store=build_vector_store(s),
        embedding=build_embedding(s),
        llm=SlowLLM(),
        reranker=build_reranker(s),
    )


async def asgi_call(app, method: str, path: str, *, json_body=None, token=None):
    """直接以 ASGI 协议调用应用，返回 (状态码, [(耗时秒, 文本片段)])。"""
    headers: list[tuple[bytes, bytes]] = []
    body = b""
    if json_body is not None:
        body = json.dumps(json_body).encode()
        headers.append((b"content-type", b"application/json"))
    headers.append((b"content-length", str(len(body)).encode()))
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))

    frames: list[tuple[float, str]] = []
    answered = {"done": False}

    async def receive():
        if not answered["done"]:
            answered["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        # 不能返回 http.disconnect：StreamingResponse 的断连监听会取消正在跑的流
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    t0 = time.perf_counter()
    state = {"status": 0}

    async def send(message):
        if message["type"] == "http.response.start":
            state["status"] = message["status"]
        elif message["type"] == "http.response.body":
            chunk = message.get("body") or b""
            if chunk:
                frames.append((time.perf_counter() - t0, chunk.decode("utf-8", "replace")))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("local", 80),
    }
    await app(scope, receive, send)
    return state["status"], frames


async def main() -> None:
    chat_api._build_rag = build_slow_pipeline  # 把慢模型接到接口上

    from app.main import app

    s = get_settings()
    _, session_factory = init_db(s)
    app.state.session_factory = session_factory  # 等价于 lifespan 的启动动作

    await asgi_call(app, "POST", "/api/auth/register",
                    json_body={"username": "k1lat", "password": "secret123"})
    _, frames = await asgi_call(app, "POST", "/api/auth/login",
                                json_body={"username": "k1lat", "password": "secret123"})
    token = json.loads("".join(f[1] for f in frames))["access_token"]

    _, frames = await asgi_call(app, "POST", "/api/kbs",
                                json_body={"name": "K1 延迟实测"}, token=token)
    kb_id = json.loads("".join(f[1] for f in frames))["id"]

    doc_path = TMP / "policy.txt"
    doc_path.write_text(DOC_TEXT, encoding="utf-8")
    IngestionPipeline(
        settings=s,
        session_factory=session_factory,
        vector_store=build_vector_store(s),
        embedding=build_embedding(s),
    ).ingest_file(kb_id, doc_path)

    # ---- 改造前等价物：首字节 = rag.ask() 返回的时刻（那时才轮到 event_stream 开始 yield）----
    t0 = time.perf_counter()
    build_slow_pipeline().ask(kb_id, "出差住宿标准是多少？")
    old_first_byte = time.perf_counter() - t0

    # ---- 改造后：真流式 ----
    status, frames = await asgi_call(
        app, "POST", f"/api/kbs/{kb_id}/ask/stream",
        json_body={"query": "出差住宿标准是多少？", "mode": "hybrid"}, token=token,
    )
    assert status == 200, status

    marks: dict[str, float] = {}
    buf = ""
    for ts, chunk in frames:
        buf += chunk
        marks.setdefault("first_byte", ts)
        for block in buf.split("\n\n")[:-1]:
            if not block.startswith("data: "):
                continue
            payload = block[6:]
            if payload == "[DONE]":
                continue
            evt = json.loads(payload)
            if evt["type"] == "sources":
                marks.setdefault("sources", ts)
            elif evt["type"] == "token":
                marks.setdefault("first_token", ts)
            elif evt["type"] == "done":
                marks["done"] = ts
        buf = buf.split("\n\n")[-1]

    print("=" * 60)
    print(f"桩模型：首 token 前阻塞 {FIRST_TOKEN_DELAY}s，之后每 {TOKEN_INTERVAL}s 一个 token")
    print(f"答案 {len(ANSWER_TOKENS)} 个 token → 整段理论耗时 "
          f"{FIRST_TOKEN_DELAY + len(ANSWER_TOKENS) * TOKEN_INTERVAL:.1f}s")
    print("=" * 60)
    print(f"改造前 首字节（= 等整段生成）: {old_first_byte:6.2f} s")
    print(f"改造后 首字节（stage 事件）  : {marks.get('first_byte', -1):6.2f} s")
    print(f"改造后 sources（引用可见）   : {marks.get('sources', -1):6.2f} s")
    print(f"改造后 首个 token            : {marks.get('first_token', -1):6.2f} s")
    print(f"改造后 整段结束              : {marks.get('done', -1):6.2f} s")
    if "first_byte" in marks:
        print(f"\n首字节提速 {old_first_byte / max(marks['first_byte'], 1e-6):.0f}× "
              f"：{old_first_byte:.2f}s → {marks['first_byte'] * 1000:.0f}ms")


asyncio.run(main())
