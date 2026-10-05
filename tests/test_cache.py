"""K6 问答缓存测试 —— 全部**离线可跑**。

分两层：

1. 编排层（stub vector store + 受控 embedding/LLM）：精确命中与规范化键、
   多轮不缓存、语义命中与阈值边界、LRU 淘汰、按库失效、缓存故障降级
2. 接口层（DATA_DIR → tmp_path，mock 供应商）：同问两次 `cache_hit`、
   参数不同不命中、流式重放 `done` 带 `cache_hit`、文档删除后失效
"""
from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.providers.base import EmbeddingProvider, LLMProvider, RerankProvider
from app.rag.pipeline import RagPipeline
from app.rag.query_cache import normalize_query
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


class _CountingLLM(LLMProvider):
    """数调用次数的假 LLM：缓存命中与否直接体现在 calls 上。"""

    def __init__(self, answer: str = "根据资料，住宿按城市分档报销。") -> None:
        self.answer = answer
        self.calls = 0

    def complete(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
        system: str | None = None,
        model: str | None = None,
    ) -> str:
        self.calls += 1
        return self.answer

    def stream(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
        system: str | None = None,
        model: str | None = None,
    ) -> Iterator[str]:
        self.calls += 1
        yield self.answer


class _DictEmbedding(EmbeddingProvider):
    """受控向量：显式分组的文本用指定向量，其余按字符 8 桶词袋（确定性、
    不同问题天然低相似）。"""

    DIM = 2

    def __init__(self, groups: dict[str, list[float]] | None = None) -> None:
        self.groups = groups or {}

    @property
    def dim(self) -> int:
        return self.DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(self.groups.get(t) or self._bag(t)) for t in texts]

    @staticmethod
    def _bag(text: str) -> list[float]:
        vec = [0.0] * 8
        for ch in text:
            vec[ord(ch) % 8] += 1.0
        return vec


def _make_pipeline(
    tmp_path: Path,
    *,
    llm: LLMProvider | None = None,
    embedding: EmbeddingProvider | None = None,
    **settings_overrides,
) -> RagPipeline:
    settings = Settings(
        _env_file=None, data_dir=tmp_path / "unit-data", **settings_overrides
    )
    _, session_factory = init_db(settings)
    return RagPipeline(
        settings=settings,
        session_factory=session_factory,
        vector_store=_StubVectorStore(),
        embedding=embedding or _DictEmbedding(),
        llm=llm or _CountingLLM(),
        reranker=_NoopRerank(),
    )


# ---------------------------------------------------------------- 编排层


def test_normalize_query_folds_whitespace_and_case() -> None:
    assert normalize_query("  出差  住宿标准 ") == "出差 住宿标准"
    assert normalize_query("Top-K Value") == "top-k value"


def test_exact_hit_second_ask_skips_llm(tmp_path) -> None:
    llm = _CountingLLM()
    p = _make_pipeline(tmp_path, llm=llm)

    a1 = p.ask(1, "出差住宿标准是多少？")
    a2 = p.ask(1, "出差住宿标准是多少？")

    assert llm.calls == 1  # 第二次完全没碰 LLM
    assert a1.cache_hit is False and a2.cache_hit is True
    assert a2.answer == a1.answer
    assert [s.filename for s in a2.sources] == [s.filename for s in a1.sources]


def test_exact_hit_uses_normalized_key(tmp_path) -> None:
    llm = _CountingLLM()
    p = _make_pipeline(tmp_path, llm=llm)

    p.ask(1, "出差住宿标准是多少？")
    p.ask(1, " 出差住宿标准是多少？  ")  # 仅空白差异

    assert llm.calls == 1


def test_different_params_miss_without_semantic(tmp_path) -> None:
    """关掉语义层后，mode / top_k 任一不同都是真 miss（精确键四元组全等才命中）。"""
    llm = _CountingLLM()
    p = _make_pipeline(tmp_path, llm=llm, cache_semantic=False)

    p.ask(1, "出差住宿标准是多少？", top_k=5)
    p.ask(1, "出差住宿标准是多少？", top_k=3)  # top_k 不同 → 键不同
    p.ask(1, "出差住宿标准是多少？", top_k=5, mode="vector")  # mode 不同 → 键不同

    assert llm.calls == 3


def test_same_query_different_top_k_miss(tmp_path) -> None:
    """设计钉子：语义命中限定同 mode + top_k + model —— 只对**问法**宽容。
    参数不同（如对话页改了「条数」）必须 miss，否则参数会被缓存静默吞掉
    （这一版设计修正是 top_k 不参与键导致参数栏失效的测试照出来的）。"""
    llm = _CountingLLM()
    p = _make_pipeline(tmp_path, llm=llm)

    p.ask(1, "出差住宿标准是多少？", top_k=5)
    hit = p.ask(1, "出差住宿标准是多少？", top_k=2)

    assert hit.cache_hit is False
    assert llm.calls == 2


def test_same_query_different_model_miss(tmp_path) -> None:
    """K7「快慢自选」不能被缓存吞掉：换模型必须重新生成。"""
    llm = _CountingLLM()
    p = _make_pipeline(tmp_path, llm=llm)

    p.ask(1, "出差住宿标准是多少？", model="fast-model")
    hit = p.ask(1, "出差住宿标准是多少？", model="slow-model")

    assert hit.cache_hit is False
    assert llm.calls == 2


def test_multiturn_never_reads_or_writes_cache(tmp_path) -> None:
    llm = _CountingLLM()
    p = _make_pipeline(tmp_path, llm=llm)
    history = [SimpleNamespace(role="user", content="之前问了什么")]

    p.ask(1, "出差住宿标准是多少？", history=history)
    p.ask(1, "出差住宿标准是多少？", history=history)
    p.ask(1, "出差住宿标准是多少？")  # 多轮从未写过缓存 → 单轮首问也是 miss

    assert llm.calls == 3


def test_semantic_hit_and_miss(tmp_path) -> None:
    groups = {
        "报销时效是多少天": [1.0, 0.0],
        "差旅报销要多久到账": [1.0, 0.0],  # 与上句同组 = 同义
        "年假有几天": [0.0, 1.0],  # 正交 = 不同意图
    }
    llm = _CountingLLM()
    p = _make_pipeline(tmp_path, llm=llm, embedding=_DictEmbedding(groups))

    p.ask(1, "报销时效是多少天")
    hit = p.ask(1, "差旅报销要多久到账")  # 余弦 1.0 ≥ 0.95 → 语义命中
    p.ask(1, "年假有几天")  # 余弦 0 → 不许命中

    assert hit.cache_hit is True
    assert llm.calls == 2  # 同义问命中 + 正交问未命中


def test_semantic_can_be_disabled(tmp_path) -> None:
    groups = {"报销时效是多少天": [1.0, 0.0], "差旅报销要多久到账": [1.0, 0.0]}
    llm = _CountingLLM()
    p = _make_pipeline(
        tmp_path, llm=llm, embedding=_DictEmbedding(groups), cache_semantic=False
    )

    p.ask(1, "报销时效是多少天")
    p.ask(1, "差旅报销要多久到账")

    assert llm.calls == 2


def test_semantic_threshold_boundary_is_inclusive(tmp_path) -> None:
    groups = {"报销时效是多少天": [1.0, 0.0], "差旅报销要多久到账": [1.0, 0.0]}
    llm = _CountingLLM()
    p = _make_pipeline(
        tmp_path, llm=llm, embedding=_DictEmbedding(groups), cache_semantic_threshold=1.0
    )

    p.ask(1, "报销时效是多少天")
    hit = p.ask(1, "差旅报销要多久到账")  # 余弦恰好 1.0，>= 阈值 → 命中

    assert hit.cache_hit is True
    assert llm.calls == 1


def test_lru_eviction(tmp_path) -> None:
    groups = {"问题甲": [1.0, 0.0], "问题乙": [0.0, 1.0]}
    llm = _CountingLLM()
    p = _make_pipeline(
        tmp_path, llm=llm, embedding=_DictEmbedding(groups), cache_max_entries=1
    )

    p.ask(1, "问题甲")
    p.ask(1, "问题乙")  # 超限 → 甲（最旧）被淘汰
    p.ask(1, "问题甲")  # miss（重新写入并淘汰乙）
    p.ask(1, "问题乙")  # 上一轮乙已被淘汰 → miss

    assert llm.calls == 4


def test_invalidate_kb(tmp_path) -> None:
    llm = _CountingLLM()
    p = _make_pipeline(tmp_path, llm=llm)

    p.ask(1, "出差住宿标准是多少？")
    assert p.cache.invalidate_kb(1) == 1
    p.ask(1, "出差住宿标准是多少？")

    assert llm.calls == 2


def test_cache_failure_degrades_to_normal_flow(tmp_path, monkeypatch) -> None:
    llm = _CountingLLM()
    p = _make_pipeline(tmp_path, llm=llm)

    def _boom(*args, **kwargs):
        raise RuntimeError("缓存表坏了")

    monkeypatch.setattr(p.cache, "lookup", _boom)
    monkeypatch.setattr(p.cache, "store", _boom)

    a1 = p.ask(1, "出差住宿标准是多少？")  # 读失败 → 按未命中走原链路
    a2 = p.ask(1, "出差住宿标准是多少？")  # 写失败 → 第二次仍 miss，但不报错

    assert a1.answer and a2.answer
    assert a1.cache_hit is False and a2.cache_hit is False
    assert llm.calls == 2


def test_default_cache_semantic_threshold_in_measured_gap() -> None:
    """护栏：语义命中阈值默认值必须落在实测间隙内（K3 相似度阈值同款防回归）。

    实测（2026-10-05，qwen3.7-text-embedding，20 对探针，全 kb_1/kb_2 语料域）：
      同义改写对 10 对最低 0.8092；易混意图对 8 对最高 0.7559。
    ⚠️ 边界值来自当前演示语料 + 当前 embedding：换供应商或大改语料必须重测
    （python scripts/k6_cache_eval.py），否则这个默认值就是过拟合。
    """
    same_intent_min = 0.8092
    diff_intent_max = 0.7559
    default = Settings(_env_file=None).cache_semantic_threshold

    assert diff_intent_max < default < same_intent_min, (
        f"默认阈值 {default} 不再落在实测间隙 [{diff_intent_max}, {same_intent_min}] 内，"
        "请先重测（scripts/k6_cache_eval.py）再改，不要拍脑袋上调。"
    )


# ---------------------------------------------------------------- 接口层


def _register_and_login(client: TestClient, username: str) -> str:
    client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    return client.post(
        "/api/auth/login", json={"username": username, "password": "secret123"}
    ).json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _kb_with_doc(client: TestClient, username: str) -> tuple[str, int]:
    token = _register_and_login(client, username)
    kb_id = client.post(
        "/api/kbs", json={"name": f"test_{username}_kb"}, headers=_auth(token)
    ).json()["id"]
    resp = client.post(
        f"/api/kbs/{kb_id}/documents",
        files={"file": ("policy.txt", io.BytesIO("出差住宿标准：一线城市每晚不超过六百元。".encode()), "text/plain")},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return token, kb_id


def test_api_ask_cache_hit(client: TestClient) -> None:
    token, kb_id = _kb_with_doc(client, "test_k6_api")

    r1 = client.post(
        f"/api/kbs/{kb_id}/ask", json={"query": "出差住宿标准是多少？"}, headers=_auth(token)
    ).json()
    r2 = client.post(
        f"/api/kbs/{kb_id}/ask", json={"query": "出差住宿标准是多少？"}, headers=_auth(token)
    ).json()

    assert r1["cache_hit"] is False
    assert r2["cache_hit"] is True
    assert r2["answer"] == r1["answer"]
    assert r2["sources"] == r1["sources"]


def test_api_ask_different_query_miss(client: TestClient) -> None:
    """不同问题（不同 top_k）→ 精确与语义都不命中。"""
    token, kb_id = _kb_with_doc(client, "test_k6_topk")

    client.post(
        f"/api/kbs/{kb_id}/ask", json={"query": "出差住宿标准是多少？"}, headers=_auth(token)
    )
    r2 = client.post(
        f"/api/kbs/{kb_id}/ask",
        json={"query": "年假有多少天？", "top_k": 1},
        headers=_auth(token),
    ).json()

    assert r2["cache_hit"] is False


def test_api_stream_replays_cached_answer(client: TestClient) -> None:
    token, kb_id = _kb_with_doc(client, "test_k6_stream")
    url = f"/api/kbs/{kb_id}/ask/stream"

    first = client.post(url, json={"query": "出差住宿标准是多少？"}, headers=_auth(token))
    assert first.status_code == 200
    second = client.post(url, json={"query": "出差住宿标准是多少？"}, headers=_auth(token))
    assert second.status_code == 200

    events = []
    for block in second.text.split("\n\n"):
        line = block.strip()
        if line.startswith("data: ") and line != "data: [DONE]":
            events.append(json.loads(line[6:]))
    tokens = [e["content"] for e in events if e["type"] == "token"]
    done = next(e for e in events if e["type"] == "done")

    assert done["cache_hit"] is True
    assert done["answer"] == "".join(tokens)  # 缓存答案按段重放，拼接仍完整
    sources_events = [e for e in events if e["type"] == "sources"]
    assert sources_events and sources_events[0]["sources"]  # sources 照常先发


def test_api_invalidate_on_document_delete(client: TestClient) -> None:
    token, kb_id = _kb_with_doc(client, "test_k6_inv")
    ask_url = f"/api/kbs/{kb_id}/ask"

    client.post(ask_url, json={"query": "出差住宿标准是多少？"}, headers=_auth(token))
    assert (
        client.post(ask_url, json={"query": "出差住宿标准是多少？"}, headers=_auth(token))
        .json()["cache_hit"]
        is True
    )

    doc_id = client.get(
        f"/api/kbs/{kb_id}/documents", headers=_auth(token)
    ).json()[0]["id"]
    resp = client.delete(f"/api/documents/{doc_id}", headers=_auth(token))
    assert resp.status_code == 200

    r3 = client.post(ask_url, json={"query": "出差住宿标准是多少？"}, headers=_auth(token)).json()
    assert r3["cache_hit"] is False  # 文档删除 → 该库缓存全量失效
