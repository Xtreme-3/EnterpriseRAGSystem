"""K1 真流式生成测试。

分三层，全部**离线可跑**（mock 供应商 + 临时目录，不依赖 PostgreSQL / 真实 Key）：

1. Provider 层：``MockLLM.stream`` 与 ``complete`` 等价、基类默认实现、OpenAICompat 的 delta 解析
2. 编排层：``RagPipeline.ask_stream`` 的事件顺序（sources 必须早于首个 token）
3. 接口层：``/ask/stream`` 的 SSE 序列、落库后置、流内 error 事件

第 3 层用 monkeypatch 把 DATA_DIR 指到 tmp_path，因此不会碰真实 data/ 目录。
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.providers.base import LLMProvider, RerankProvider
from app.providers.mock import MockEmbedding, MockLLM
from app.providers.openai_compat import OpenAICompatLLM
from app.rag.generator import NO_HIT_ANSWER, Generator
from app.rag.pipeline import RagPipeline
from app.storage.db import init_db
from app.storage.vector_store import ChunkToIndex, ScoredChunk, VectorStore

# ---------------------------------------------------------------- 测试替身

PROMPT = """你是企业内部知识库问答助手。

【资料】
[1] 出差住宿标准：一线城市每晚不超过六百元。

【用户问题】
住宿标准是多少？

要求：……
"""


class _StubLLM(LLMProvider):
    """记录调用的可流式 LLM。``tokens`` 决定 stream 产出，``boom_at`` 用于模拟中途异常。"""

    def __init__(self, tokens: list[str] | None = None, boom_at: int | None = None) -> None:
        self.tokens = tokens if tokens is not None else ["答案", "是", "六百元"]
        self.boom_at = boom_at
        self.complete_calls = 0
        self.stream_calls = 0

    def complete(self, prompt: str, *, max_tokens: int = 1024) -> str:
        self.complete_calls += 1
        return "".join(self.tokens)

    def stream(self, prompt: str, *, max_tokens: int = 1024) -> Iterator[str]:
        self.stream_calls += 1
        for i, tok in enumerate(self.tokens):
            if self.boom_at is not None and i == self.boom_at:
                raise RuntimeError("模型连接中断")
            yield tok


class _StubVectorStore(VectorStore):
    """固定召回一条切片，避免测试依赖 chroma / pgvector。"""

    def __init__(self, hits: list[ScoredChunk] | None = None) -> None:
        self.hits = hits if hits is not None else [
            ScoredChunk(
                document_id=1,
                kb_id=1,
                chunk_index=0,
                content="出差住宿标准：一线城市每晚不超过六百元。",
                score=0.91,
            )
        ]

    def ensure_collection(self, kb_id: int, dim: int) -> None:
        pass

    def add(self, kb_id: int, chunks: list[ChunkToIndex]) -> None:
        pass

    def search(self, kb_id: int, vector: list[float], top_k: int) -> list[ScoredChunk]:
        return list(self.hits[:top_k])

    def search_lexical(self, kb_id: int, query: str, top_k: int) -> list[ScoredChunk]:
        return list(self.hits[:top_k])

    def delete_document(self, kb_id: int, document_id: int) -> None:
        pass

    def delete_collection(self, kb_id: int) -> None:
        pass

    def document_counts(self, kb_id: int) -> dict[int, int]:
        return {}


class _NoopRerank(RerankProvider):
    def rerank(self, query: str, texts: list[str], scores: list[float]) -> list[float]:
        return list(scores)


# ---------------------------------------------------------------- 1. Provider 层


def test_mock_llm_stream_equals_complete() -> None:
    """基类默认实现：MockLLM 不覆写 stream，拼接结果必须与 complete 完全一致（离线零回归）。"""
    llm = MockLLM()
    assert "".join(llm.stream(PROMPT)) == llm.complete(PROMPT)


def test_mock_llm_stream_no_hit_returns_fixed_text() -> None:
    """检索块为空时 MockLLM 的流式输出同样给出固定文案。"""
    llm = MockLLM()
    prompt = PROMPT.replace("[1] 出差住宿标准：一线城市每晚不超过六百元。\n", "")
    assert "".join(llm.stream(prompt)) == llm.complete(prompt)


def test_base_stream_default_yields_complete_once() -> None:
    """未覆写 stream 的实现：恰好产出一段，且等于 complete 的结果。"""

    class _Only(LLMProvider):
        def complete(self, prompt: str, *, max_tokens: int = 1024) -> str:
            return "整段答案"

    assert list(_Only().stream("p")) == ["整段答案"]


def _openai_stub_chunks() -> list:
    """模拟 openai SDK 的 chunk 序列：含空 choices、reasoning_content、content=None 三种噪声。"""
    return [
        SimpleNamespace(choices=[]),  # usage-only chunk
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            content=None, reasoning_content="先想一想"))]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="", reasoning_content=None))]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="六百", reasoning_content=None))]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="元", reasoning_content=None))]),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, reasoning_content=None))]),
    ]


def test_openai_compat_stream_orders_and_filters() -> None:
    """只取 delta.content：跳过空 choices / None / 空串，丢弃 reasoning_content，顺序不变。"""
    captured: list[dict] = []
    llm = OpenAICompatLLM(base_url="http://stub/v1", api_key="k", model="qwen3.8-flash")
    llm._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kw: (captured.append(kw), iter(_openai_stub_chunks()))[1]
        ))
    )

    assert list(llm.stream("问个问题")) == ["六百", "元"]
    assert captured[0]["stream"] is True
    assert captured[0]["temperature"] == 0.2


def test_openai_compat_complete_still_non_streaming() -> None:
    """complete() 未被 K1 改坏：仍是非流式调用，取 message.content。"""
    captured: list[dict] = []
    resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="完整答案"))])
    llm = OpenAICompatLLM(base_url="http://stub/v1", api_key="k", model="m")
    llm._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kw: (captured.append(kw), resp)[1]
        ))
    )

    assert llm.complete("问题") == "完整答案"
    assert captured[0]["stream"] is False


# ---------------------------------------------------------------- 2. Generator / Pipeline 层


def test_generator_stream_empty_chunks_skips_llm() -> None:
    """检索为空 → 不调用大模型，直接给固定文案。"""
    llm = _StubLLM()
    gen = Generator(llm)
    assert list(gen.stream("问题", [])) == [NO_HIT_ANSWER]
    assert llm.stream_calls == 0
    assert llm.complete_calls == 0


def test_generator_stream_matches_generate() -> None:
    """有切片时 stream 拼接 == generate 结果。"""
    llm = _StubLLM()
    gen = Generator(llm)
    hits = _StubVectorStore().hits
    assert "".join(gen.stream("问题", hits)) == gen.generate("问题", hits)


@pytest.fixture()
def pipeline(tmp_path) -> tuple[RagPipeline, _StubLLM]:
    settings = Settings(
        rag_provider="mock",
        data_dir=tmp_path / "data",
        top_k=3,
        vector_store="chroma",
    )
    _, session_factory = init_db(settings)
    llm = _StubLLM()
    rag = RagPipeline(
        settings=settings,
        session_factory=session_factory,
        vector_store=_StubVectorStore(),
        embedding=MockEmbedding(),
        llm=llm,
        reranker=_NoopRerank(),
    )
    return rag, llm


def _drain(rag: RagPipeline) -> list[dict]:
    return list(rag.ask_stream(kb_id=1, query="住宿标准是多少？"))


def test_ask_stream_event_order(pipeline) -> None:
    """事件顺序：stage(rewriting) → stage(retrieving) → stage(reranking) → sources → token+ → done。"""
    rag, _ = pipeline
    events = _drain(rag)

    stages = [e["stage"] for e in events if e["type"] == "stage"]
    assert stages == ["rewriting", "retrieving", "reranking"]

    types = [e["type"] for e in events]
    assert types[0] == "stage"
    assert types[-1] == "done"
    assert types.count("sources") == 1
    assert types.count("token") == 3


def test_ask_stream_sources_before_first_token(pipeline) -> None:
    """★ 体验核心断言：sources 必须在第一个 token 之前到达。"""
    rag, _ = pipeline
    events = _drain(rag)

    first_sources = next(i for i, e in enumerate(events) if e["type"] == "sources")
    first_token = next(i for i, e in enumerate(events) if e["type"] == "token")
    assert first_sources < first_token

    sources_evt = events[first_sources]
    assert sources_evt["stage"] == "generating"
    assert len(sources_evt["sources"]) == 1
    assert sources_evt["sources"][0].content.startswith("出差住宿标准")


def test_ask_stream_done_carries_full_answer(pipeline) -> None:
    """done 事件携带的 answer 等于所有 token 拼接，供调用方落库。"""
    rag, llm = pipeline
    events = _drain(rag)

    joined = "".join(e["content"] for e in events if e["type"] == "token")
    done = events[-1]
    assert done["answer"] == joined == "".join(llm.tokens)
    assert len(done["sources"]) == 1  # 完整来源（非 SSE 截断版）


def test_ask_stream_empty_retrieval(pipeline) -> None:
    """检索为空 → sources 为空 + 固定文案，不调 LLM。"""
    rag, llm = pipeline
    rag.retriever.vector_store = _StubVectorStore(hits=[])

    events = _drain(rag)
    sources_evt = next(e for e in events if e["type"] == "sources")
    assert sources_evt["sources"] == []

    tokens = "".join(e["content"] for e in events if e["type"] == "token")
    assert tokens == NO_HIT_ANSWER
    assert llm.stream_calls == 0


def test_ask_stream_propagates_llm_error(pipeline) -> None:
    """生成中途异常由生成器抛出（接口层负责转成 error 事件）。"""
    rag, _ = pipeline
    rag.generator = Generator(_StubLLM(boom_at=1))

    with pytest.raises(RuntimeError):
        _drain(rag)


def test_ask_behaviour_unchanged(pipeline) -> None:
    """ask()（非流式）不受 K1 影响：同文案、同来源、同改写问句。"""
    rag, llm = pipeline
    result = rag.ask(1, "住宿标准是多少？")

    assert result.answer == "".join(llm.tokens)
    assert llm.complete_calls == 1
    assert llm.stream_calls == 0
    assert len(result.sources) == 1
    assert result.sources[0].filename == "doc_1"  # 库内无该文档 → 兜底名
    assert result.rewritten_query == "住宿标准是多少？"


# ---------------------------------------------------------------- 3. 接口层（离线 SSE）


@pytest.fixture()
def api(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """离线 API 环境：临时 DATA_DIR + 全 mock 供应商，绝不触碰真实 data/ 目录。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VECTOR_STORE", "chroma")
    monkeypatch.setenv("RAG_PROVIDER", "mock")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("CHUNK_SIZE", "200")
    monkeypatch.setenv("CHUNK_OVERLAP", "20")
    monkeypatch.setenv("TOP_K", "3")

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_and_login(client: TestClient, username: str = "test_k1_user") -> str:
    client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    return client.post(
        "/api/auth/login", json={"username": username, "password": "secret123"}
    ).json()["access_token"]


def _setup_kb(client: TestClient, token: str) -> int:
    kb_id = client.post(
        "/api/kbs", json={"name": "test_k1_kb"}, headers=_auth(token)
    ).json()["id"]
    client.post(
        f"/api/kbs/{kb_id}/documents",
        files={"file": ("policy.txt",
                        "出差住宿标准：一线城市每晚不超过六百元，其他城市不超过四百元。".encode("utf-8"),
                        "text/plain")},
        headers=_auth(token),
    )
    return kb_id


def _parse_sse(text: str) -> list[tuple[str, object]]:
    """把 SSE 响应体解析成 [(kind, payload)]，kind = comment | event | done。"""
    out: list[tuple[str, object]] = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith(":"):
            out.append(("comment", block))
            continue
        for line in block.split("\n"):
            line = line.strip()
            if not line.startswith("data: "):
                continue
            data = line[6:]
            out.append(("done", None) if data == "[DONE]" else ("event", json.loads(data)))
    return out


def test_api_stream_event_sequence(api: TestClient) -> None:
    """SSE 序列合规：心跳注释存在、stage 顺序正确、sources 早于首个 token、以 [DONE] 收尾。"""
    token = _register_and_login(api)
    kb_id = _setup_kb(api, token)

    resp = api.post(
        f"/api/kbs/{kb_id}/ask/stream",
        json={"query": "出差住宿标准是多少？", "mode": "hybrid"},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")

    parts = _parse_sse(resp.text)
    events = [p for k, p in parts if k == "event"]
    assert [k for k, _ in parts].count("comment") >= 3          # 每个 stage 前一行心跳
    assert parts[-1][0] == "done"                               # 以 data: [DONE] 结尾

    stages = [e["stage"] for e in events if e["type"] == "stage"]  # type: ignore[index]
    assert stages == ["rewriting", "retrieving", "reranking"]

    idx_sources = next(i for i, e in enumerate(events) if e["type"] == "sources")  # type: ignore[index]
    idx_token = next(i for i, e in enumerate(events) if e["type"] == "token")      # type: ignore[index]
    assert idx_sources < idx_token

    sources_evt = events[idx_sources]
    assert sources_evt["sources"][0]["filename"] == "policy.txt"  # type: ignore[index]
    assert sources_evt["stage"] == "generating"                    # type: ignore[index]

    done_evt = next(e for e in events if e["type"] == "done")      # type: ignore[index]
    joined = "".join(e["content"] for e in events if e["type"] == "token")  # type: ignore[index]
    assert done_evt["answer"] == joined and joined                    # type: ignore[index]
    assert "六百元" in joined


def test_api_stream_persists_after_done(api: TestClient) -> None:
    """★ 落库后置：带 conversation_id 时，assistant 消息在流结束后完整落库（非空、非半条）。"""
    token = _register_and_login(api, "test_k1_conv")
    kb_id = _setup_kb(api, token)
    conv = api.post(f"/api/kbs/{kb_id}/conversations", json={},
                    headers=_auth(token)).json()
    assert conv["title"] == "新对话"

    resp = api.post(
        f"/api/kbs/{kb_id}/ask/stream",
        json={"query": "出差住宿标准是多少？", "conversation_id": conv["id"]},
        headers=_auth(token),
    )
    assert resp.status_code == 200

    events = [p for k, p in _parse_sse(resp.text) if k == "event"]
    done_evt = next(e for e in events if e["type"] == "done")  # type: ignore[index]
    assert done_evt["conversation_id"] == conv["id"]           # type: ignore[index]

    detail = api.get(
        f"/api/kbs/{kb_id}/conversations/{conv['id']}", headers=_auth(token)
    ).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][0]["content"] == "出差住宿标准是多少？"

    assistant = detail["messages"][1]
    assert assistant["content"].strip()                       # 不是空答案
    assert assistant["content"] == done_evt["answer"]         # 与下发内容一致（非半条）
    assert len(assistant["sources"]) >= 1
    assert assistant["sources"][0]["filename"] == "policy.txt"

    # 会话活跃时间与标题已更新（与 ask() 行为一致）
    assert detail["title"] == "出差住宿标准是多少？"


def test_api_stream_qa_log_written(api: TestClient) -> None:
    """落库后置同时覆盖 QaLog（G4）：流结束后问答历史可查到。"""
    token = _register_and_login(api, "test_k1_log")
    kb_id = _setup_kb(api, token)

    api.post(f"/api/kbs/{kb_id}/ask/stream", json={"query": "住宿标准"},
             headers=_auth(token))

    logs = api.get(f"/api/kbs/{kb_id}/qa-logs", headers=_auth(token)).json()
    assert len(logs) >= 1
    assert logs[0]["query"] == "住宿标准"
    assert logs[0]["answer"].strip()
    assert logs[0]["hit_count"] >= 1


def test_api_stream_error_becomes_event(api: TestClient, monkeypatch) -> None:
    """★ 生成期异常 → 流内 error 事件，HTTP 仍为 200，且连接正常以 [DONE] 收尾。"""
    token = _register_and_login(api, "test_k1_err")
    kb_id = _setup_kb(api, token)

    class _BoomRag:
        def ask_stream(self, *args, **kwargs):
            yield {"type": "stage", "stage": "rewriting"}
            raise RuntimeError("模拟模型中断")

    monkeypatch.setattr("app.api.chat._build_rag", lambda request: _BoomRag())

    resp = api.post(f"/api/kbs/{kb_id}/ask/stream", json={"query": "随便问"},
                    headers=_auth(token))
    assert resp.status_code == 200

    parts = _parse_sse(resp.text)
    events = [p for k, p in parts if k == "event"]
    assert any(e["type"] == "error" for e in events)   # type: ignore[index]
    assert parts[-1][0] == "done"


def test_api_stream_empty_kb_no_error(api: TestClient) -> None:
    """空知识库：sources 为空、固定文案、无 error 事件。"""
    token = _register_and_login(api, "test_k1_empty")
    kb_id = api.post("/api/kbs", json={"name": "test_k1_empty_kb"},
                     headers=_auth(token)).json()["id"]

    resp = api.post(f"/api/kbs/{kb_id}/ask/stream", json={"query": "随便问"},
                    headers=_auth(token))
    assert resp.status_code == 200

    events = [p for k, p in _parse_sse(resp.text) if k == "event"]
    assert not any(e["type"] == "error" for e in events)  # type: ignore[index]
    sources_evt = next(e for e in events if e["type"] == "sources")  # type: ignore[index]
    assert sources_evt["sources"] == []                   # type: ignore[index]
    joined = "".join(e["content"] for e in events if e["type"] == "token")  # type: ignore[index]
    assert joined == NO_HIT_ANSWER
