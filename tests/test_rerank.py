"""I1：重排序单元测试 —— Noop / Mock / OpenAICompatRerank / Reranker 装配与分数语义。

全部离线：MockRerank 用 H1 tokenize 词重叠打分，OpenAICompatRerank 只测静态解析。
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.providers.factory import (
    MockRerank,
    NoopRerank,
    OpenAICompatRerank,
    build_reranker,
)
from app.rag.reranker import Reranker
from app.storage.vector_store import ScoredChunk


def _chunks(*texts: str) -> list[ScoredChunk]:
    return [
        ScoredChunk(
            document_id=i + 1,
            kb_id=1,
            chunk_index=i,
            content=text,
            score=round(1.0 - i * 0.1, 4),
        )
        for i, text in enumerate(texts)
    ]


# ---- NoopRerank：恒等（回归路径） ----


def test_noop_returns_original_scores() -> None:
    scores = [0.9, 0.5, 0.3]
    assert NoopRerank().rerank("随便问", ["a", "b", "c"], scores) == scores


def test_reranker_noop_keeps_order_and_score() -> None:
    chunks = _chunks("差旅报销制度", "年假管理制度", "考勤制度")
    out = Reranker(NoopRerank()).rerank("问", chunks, top_n=2)
    # 排序与分数完全不变（回归），top_n 截断
    assert [c.score for c in out] == [1.0, 0.9]
    assert [c.content for c in out] == ["差旅报销制度", "年假管理制度"]


# ---- MockRerank：词重叠率，确定性 ----


def test_mock_rerank_ranks_overlap_highest() -> None:
    chunks = _chunks("本制度规定出差住宿标准", "本制度规定年假天数", "完全不相关的自我介绍")
    out = Reranker(MockRerank()).rerank("出差住宿标准", chunks)
    assert out[0].content == "本制度规定出差住宿标准"
    # 分数 = 词重叠率 ∈ [0,1]
    assert 0.0 <= out[0].score <= 1.0
    # 命中对象身份保留（document_id/chunk_index 不变），仅分数被更新
    assert (out[0].document_id, out[0].chunk_index) == (1, 0)


def test_mock_rerank_no_terms_falls_back_to_original() -> None:
    chunks = _chunks("abc", "def")
    out = Reranker(MockRerank()).rerank("x", chunks)  # 单字符 → tokenize 空
    assert [c.score for c in out] == [1.0, 0.9]


def test_mock_rerank_empty_chunks() -> None:
    assert Reranker(MockRerank()).rerank("问", []) == []


def test_mock_rerank_top_n_truncates() -> None:
    chunks = _chunks("含关键词甲", "含关键词甲乙", "含关键词甲丙", "无关系内容")
    out = Reranker(MockRerank()).rerank("关键词甲", chunks, top_n=2)
    assert len(out) == 2
    assert all("关键词" in c.content for c in out)


# ---- OpenAICompatRerank：响应解析（离线，按 index 对齐） ----


def test_parse_relevance_score_by_index() -> None:
    payload = {
        "results": [
            {"index": 2, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.3},
            {"index": 1, "relevance_score": 0.7},
        ]
    }
    assert OpenAICompatRerank._parse(payload, n=3) == [0.3, 0.7, 0.9]


def test_parse_fallback_score_field() -> None:
    payload = {"results": [{"index": 0, "score": 1.5}]}
    assert OpenAICompatRerank._parse(payload, n=1) == [1.5]


def test_parse_missing_results_defaults_zero() -> None:
    assert OpenAICompatRerank._parse({}, n=2) == [0.0, 0.0]
    assert OpenAICompatRerank._parse({"results": None}, n=2) == [0.0, 0.0]


def test_parse_out_of_range_index_ignored() -> None:
    payload = {"results": [{"index": 9, "relevance_score": 1.0}]}
    assert OpenAICompatRerank._parse(payload, n=2) == [0.0, 0.0]


# ---- build_reranker：按配置装配 ----


def test_build_reranker_off_returns_noop() -> None:
    assert isinstance(build_reranker(Settings(rag_provider="mock", rerank=False)), NoopRerank)
    # 默认（未开重排）也是 Noop
    assert isinstance(build_reranker(Settings(rag_provider="mock")), NoopRerank)


def test_build_reranker_mock_on_returns_mock() -> None:
    assert isinstance(build_reranker(Settings(rag_provider="mock", rerank=True)), MockRerank)


def test_build_reranker_real_without_key_raises(monkeypatch) -> None:
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API Key"):
        build_reranker(Settings(_env_file=None, rag_provider="dashscope", rerank=True))


def test_build_reranker_real_returns_openai_compat(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    r = build_reranker(Settings(_env_file=None, rag_provider="dashscope", rerank=True))
    assert isinstance(r, OpenAICompatRerank)
    assert r._model == "gte-rerank"  # 默认模型
