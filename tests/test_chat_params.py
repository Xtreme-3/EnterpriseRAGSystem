"""K7 对话页参数（top_k / rerank / model）测试 —— 全部**离线可跑**。

分两层：

1. 接口层：``/api/config/chat`` 配置端点、AskRequest 校验（422）、参数贯通
   （top_k 限制来源条数、rerank=true 在 mock 供应商下走通强制重排）。
   用 monkeypatch 把 ``DATA_DIR`` 指到 tmp_path，不碰真实 ``data/``、不依赖 PostgreSQL。
2. 编排层：``RagPipeline._maybe_rerank`` 三态语义（None 跟随 / False 跳过 / True 强制）、
   强制重排器**只建一次**、``model`` 贯通 Generator → LLMProvider。
"""
from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.providers.base import LLMProvider, RerankProvider
from app.providers.mock import MockEmbedding, MockLLM
from app.providers.openai_compat import OpenAICompatLLM
from app.rag.pipeline import RagPipeline
from app.storage.db import init_db
from app.storage.vector_store import ChunkToIndex, ScoredChunk, VectorStore

# ---------------------------------------------------------------- 测试替身


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


class _RecordingLLM(LLMProvider):
    """记录每次调用收到的 model（K7 贯通断言用）。"""

    def __init__(self) -> None:
        self.models: list[str | None] = []

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
        system: str | None = None,
        model: str | None = None,
    ) -> str:
        self.models.append(model)
        return "根据资料，出差住宿按城市分档报销。"

    def stream(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
        system: str | None = None,
        model: str | None = None,
    ) -> Iterator[str]:
        self.models.append(model)
        yield "根据资料，出差住宿按城市分档报销。"


def _make_pipeline(
    tmp_path: Path,
    *,
    rerank_global: bool = False,
    reranker: RerankProvider | None = None,
    llm: LLMProvider | None = None,
) -> RagPipeline:
    settings = Settings(_env_file=None, data_dir=tmp_path / "unit-data", rerank=rerank_global)
    _, session_factory = init_db(settings)
    return RagPipeline(
        settings=settings,
        session_factory=session_factory,
        vector_store=_StubVectorStore(),
        embedding=MockEmbedding(),
        llm=llm or MockLLM(),
        reranker=reranker or _NoopRerank(),
    )


# ---------------------------------------------------------------- 接口层（离线，DATA_DIR → tmp）


def _register_and_login(client: TestClient, username: str) -> str:
    client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    return client.post(
        "/api/auth/login", json={"username": username, "password": "secret123"}
    ).json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_kb(client: TestClient, token: str, name: str) -> int:
    return client.post(
        "/api/kbs", json={"name": name}, headers=_auth(token)
    ).json()["id"]


def _upload(client: TestClient, kb_id: int, token: str, filename: str, content: str):
    return client.post(
        f"/api/kbs/{kb_id}/documents",
        files={"file": (filename, io.BytesIO(content.encode("utf-8")), "text/plain")},
        headers=_auth(token),
    )


def _kb_with_3_chunk_doc(client: TestClient, username: str) -> tuple[str, int]:
    """上传一份恰好切成 3 块的文档（每段 ~750 字 < chunk_size 800，段间空行分隔）。"""
    token = _register_and_login(client, username)
    kb_id = _create_kb(client, token, f"test_{username}_kb")
    part = (
        "出差住宿标准：员工出差住宿按城市分档报销。（第{i}部分）"
        + "补充：住宿费用凭发票按标准报销。" * 45
    )
    content = "\n\n".join(part.format(i=i) for i in (1, 2, 3))
    resp = _upload(client, kb_id, token, "policy.txt", content)
    assert resp.status_code == 201, resp.text
    assert resp.json()["chunk_count"] == 3  # 三段各 ~750 字，恰好 3 块
    return token, kb_id


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def test_config_chat_options(client: TestClient) -> None:
    """K7 配置端点：models 非空（llm_model 恒在首位）、rerank 可用性、默认 top_k。"""
    token = _register_and_login(client, "test_k7_cfg")
    resp = client.get("/api/config/chat", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["models"], list) and body["models"]
    assert all(isinstance(m, str) and m for m in body["models"])
    assert body["rerank"] is False  # conftest 钉 RERANK=false
    assert body["top_k_default"] >= 1


def test_config_chat_requires_auth(client: TestClient) -> None:
    assert client.get("/api/config/chat").status_code == 401


def test_ask_top_k_bounds_422(client: TestClient) -> None:
    """top_k 越界（>10 / <0）→ 422；0 = 用服务端默认，合法。"""
    token = _register_and_login(client, "test_k7_topk_bad")
    for bad in (11, -1):
        resp = client.post(
            "/api/kbs/999/ask", json={"query": "q", "top_k": bad}, headers=_auth(token)
        )
        assert resp.status_code == 422, f"top_k={bad} 应被拒绝"


def test_ask_unknown_model_422(client: TestClient) -> None:
    """model 不在服务端清单 → 422（路由层对照 app.state.settings 校验）。"""
    token = _register_and_login(client, "test_k7_model_bad")
    kb_id = _create_kb(client, token, "test_k7_model_bad_kb")
    resp = client.post(
        f"/api/kbs/{kb_id}/ask",
        json={"query": "q", "model": "no-such-model-xyz"},
        headers=_auth(token),
    )
    assert resp.status_code == 422
    assert "model" in resp.json()["detail"]


def test_ask_valid_model_and_top_k_limits_sources(client: TestClient) -> None:
    """合法 model（从配置端点取）+ top_k=1 → 恰好 1 条来源；默认 → 全部 3 块。"""
    token, kb_id = _kb_with_3_chunk_doc(client, "test_k7_ok")
    models = client.get("/api/config/chat", headers=_auth(token)).json()["models"]

    r_all = client.post(
        f"/api/kbs/{kb_id}/ask",
        json={"query": "出差住宿标准", "model": models[0]},
        headers=_auth(token),
    )
    assert r_all.status_code == 200, r_all.text
    assert len(r_all.json()["sources"]) >= 2  # 3 块近同语料，默认全部召回

    r_top1 = client.post(
        f"/api/kbs/{kb_id}/ask",
        json={"query": "出差住宿标准", "top_k": 1, "model": models[0]},
        headers=_auth(token),
    )
    assert r_top1.status_code == 200
    assert len(r_top1.json()["sources"]) == 1


def test_ask_rerank_true_works_offline(client: TestClient) -> None:
    """全局 RERANK=false 时请求 rerank=true：强制重排器 = MockRerank，链路走通不 500。"""
    token, kb_id = _kb_with_3_chunk_doc(client, "test_k7_rerank")
    resp = client.post(
        f"/api/kbs/{kb_id}/ask",
        json={"query": "出差住宿标准", "rerank": True},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["sources"]) >= 1


def test_ask_stream_with_params(client: TestClient) -> None:
    """流式主路径同样吃下 K7 参数（top_k / rerank / model），[DONE] 正常收尾。"""
    token, kb_id = _kb_with_3_chunk_doc(client, "test_k7_stream")
    models = client.get("/api/config/chat", headers=_auth(token)).json()["models"]
    resp = client.post(
        f"/api/kbs/{kb_id}/ask/stream",
        json={"query": "出差住宿标准", "top_k": 2, "rerank": False, "model": models[0]},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")
    assert "data: [DONE]" in resp.text


# ---------------------------------------------------------------- 编排层（pipeline 单元）


def test_maybe_rerank_none_and_false_skip_reranker(tmp_path, monkeypatch) -> None:
    """全局关闭时：默认（None）与显式 False 都不应触碰重排器。"""
    p = _make_pipeline(tmp_path)

    def _boom(*args, **kwargs):
        raise AssertionError("Settings.rerank=False 时不应调用重排器")

    monkeypatch.setattr(p.reranker, "rerank", _boom)
    assert p.ask(1, "出差住宿标准").answer  # None → 跟随全局（关）→ 跳过
    assert p.ask(1, "出差住宿标准", rerank=False).answer  # 显式关 → 跳过


def test_maybe_rerank_true_forces_and_caches(tmp_path, monkeypatch) -> None:
    """全局关闭 + 请求 rerank=true：按需构建强制重排器，且**只建一次**（K2）。"""
    import app.providers.factory as factory

    p = _make_pipeline(tmp_path, rerank_global=False)
    calls = {"n": 0}
    real_build = factory.build_reranker

    def _spy(settings):
        calls["n"] += 1
        return real_build(settings)

    monkeypatch.setattr(factory, "build_reranker", _spy)

    ans1 = p.ask(1, "住宿标准", rerank=True)
    ans2 = p.ask(1, "住宿标准", rerank=True)

    assert calls["n"] == 1  # 两次请求共用同一个强制重排器
    # MockRerank 按「query 分词与候选的词重叠率」打分："住宿标准" 三词全中 → 1.0
    assert ans1.sources[0].score == pytest.approx(1.0)
    assert ans2.sources[0].score == pytest.approx(1.0)


def test_maybe_rerank_global_on_uses_assembled_reranker(tmp_path, monkeypatch) -> None:
    """全局开启时用装配期实例：显式传 reranker 的情况不再触发 build_reranker。"""
    import app.providers.factory as factory

    def _boom(settings):
        raise AssertionError("全局已启用时不应再构建强制重排器")

    monkeypatch.setattr(factory, "build_reranker", _boom)
    p = _make_pipeline(tmp_path, rerank_global=True, reranker=_NoopRerank())
    assert p.ask(1, "出差住宿标准").answer
    assert p.ask(1, "出差住宿标准", rerank=False).answer  # 按问临时关闭


def test_model_passes_through_pipeline_to_llm(tmp_path) -> None:
    """model 从 RagPipeline 一路传到 LLMProvider（非流式与流式各记一次）。"""
    rec = _RecordingLLM()
    p = _make_pipeline(tmp_path, llm=rec)

    p.ask(1, "出差住宿标准", model="fast-model")
    list(p.ask_stream(1, "出差住宿标准", model="slow-model"))

    assert [m for m in rec.models if m is not None] == ["fast-model", "slow-model"]


def test_llm_model_choices_parsing() -> None:
    """清单解析：去空白、去重、llm_model 恒在首位；留空 = 只有默认一个。"""
    s = Settings(_env_file=None, llm_model="a", llm_model_options="b, a ,c,")
    assert s.llm_model_choices == ["a", "b", "c"]
    s2 = Settings(_env_file=None, llm_model="a", llm_model_options="")
    assert s2.llm_model_choices == ["a"]


def test_openai_compat_params_model_override() -> None:
    """请求体 model 字段被 per-request 参数覆盖；None 用装配时默认。"""
    llm = OpenAICompatLLM(
        base_url="https://example.invalid/v1", api_key="k", model="default-model"
    )
    assert llm._params("问题", None, None, stream=False)["model"] == "default-model"
    overridden = llm._params("问题", None, None, stream=False, model="fast-model")
    assert overridden["model"] == "fast-model"
