"""H1：混合检索测试。

- tokenize / fuse_hybrid：纯函数单测，离线
- chroma search_lexical：离线自包含（临时 chroma 目录）
- Retriever 模式切换：vector 与 H1 前行为一致（回归），hybrid 增强召回
pgvector 全文检索 / inspect mode 见 test_pgvector_store.py / test_inspect.py。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.providers.factory import build_embedding
from app.rag.hybrid import fuse_hybrid, tokenize
from app.rag.retriever import Retriever
from app.storage.vector_store import ChunkToIndex, ChromaVectorStore, ScoredChunk

KB = 1


# ---- H1-1 tokenize ----

def test_tokenize_chinese_bigram() -> None:
    assert tokenize("人工智能") == ["人工", "工智", "智能"]


def test_tokenize_ascii_words_preserved() -> None:
    # 英文/数字整词保序，单字符丢弃（"4"、"a" 无检索价值）
    assert tokenize("GPT-4 模型") == ["gpt", "模型"]


def test_tokenize_dedupe_keep_order() -> None:
    assert tokenize("hello hello world") == ["hello", "world"]


def test_tokenize_empty_or_single_char() -> None:
    assert tokenize("") == []
    assert tokenize("a") == []
    assert tokenize("!?  ") == []
    assert tokenize("A") == []


# ---- H1-1 fuse_hybrid ----

def _chunk(doc_id: int, chunk_index: int, score: float, content: str = "c") -> ScoredChunk:
    return ScoredChunk(
        document_id=doc_id, kb_id=KB, chunk_index=chunk_index, content=content, score=score
    )


def test_fuse_both_sides_sorted_and_in_range() -> None:
    a = _chunk(1, 0, 0.9, "共同命中")
    b = _chunk(1, 1, 0.5, "仅向量")
    c = _chunk(2, 0, 0.4, "仅关键词")
    out = fuse_hybrid([a, b], [_chunk(1, 0, 8.0, "共同命中"), c], top_k=3)
    assert [h.document_id for h in out] == [1, 1, 2]
    # 融合分：A = 0.5*0.9 + 0.5*(8/8) = 0.95；B = 0.5*0.5；C = 0.5*(0.4/8)
    assert abs(out[0].score - 0.95) < 1e-9
    assert abs(out[1].score - 0.25) < 1e-9
    assert abs(out[2].score - 0.025) < 1e-9
    assert all(0.0 <= h.score <= 1.0 for h in out)
    scores = [h.score for h in out]
    assert scores == sorted(scores, reverse=True)


def test_fuse_top_k_truncates() -> None:
    out = fuse_hybrid(
        [_chunk(1, 0, 0.9), _chunk(1, 1, 0.8), _chunk(1, 2, 0.7)], [], top_k=2
    )
    assert len(out) == 2


def test_fuse_only_vector_unchanged() -> None:
    hits = [_chunk(1, 0, 0.9), _chunk(2, 0, 0.8)]
    assert fuse_hybrid(hits, [], top_k=5) == hits


def test_fuse_only_lexical_unchanged() -> None:
    hits = [_chunk(1, 0, 3.0), _chunk(2, 0, 1.0)]
    assert fuse_hybrid([], hits, top_k=5) == hits


def test_fuse_both_empty() -> None:
    assert fuse_hybrid([], [], top_k=5) == []


# ---- H1-3 chroma search_lexical ----

@pytest.fixture()
def store(tmp_path: Path) -> ChromaVectorStore:
    s = ChromaVectorStore(tmp_path / "chroma")
    s.ensure_collection(KB, 8)
    return s


def test_chroma_lexical_hits_by_term_count(store: ChromaVectorStore) -> None:
    store.add(
        KB,
        [
            ChunkToIndex(id="1:0", kb_id=KB, document_id=1, chunk_index=0, content="保修期为三年", vector=[0.0] * 8),
            ChunkToIndex(id="1:1", kb_id=KB, document_id=1, chunk_index=1, content="保修服务说明", vector=[0.0] * 8),
        ],
    )
    # "保修 三年" → tokenize → ["保修", "三年"]；块0 两词都含，块1 只含"保修"
    hits = store.search_lexical(KB, "保修 三年", top_k=5)
    assert [h.chunk_index for h in hits] == [0, 1]
    assert hits[0].score == 2.0
    assert hits[1].score == 1.0


def test_chroma_lexical_empty_terms(store: ChromaVectorStore) -> None:
    store.add(KB, [ChunkToIndex(id="1:0", kb_id=KB, document_id=1, chunk_index=0, content="内容", vector=[0.0] * 8)])
    assert store.search_lexical(KB, "a", top_k=5) == []  # 单字符 → 无可用词
    assert store.search_lexical(KB, "!?", top_k=5) == []


def test_chroma_lexical_top_k(store: ChromaVectorStore) -> None:
    store.add(
        KB,
        [
            ChunkToIndex(id="1:0", kb_id=KB, document_id=1, chunk_index=0, content="A12 规格", vector=[0.0] * 8),
            ChunkToIndex(id="1:1", kb_id=KB, document_id=1, chunk_index=1, content="A12 接口", vector=[0.0] * 8),
        ],
    )
    # tokenize 统一小写 → "a12"，与内容 "A12" 大小写不敏感匹配
    hits = store.search_lexical(KB, "A12", top_k=1)
    assert len(hits) == 1
    assert hits[0].score == 1.0


# ---- H1-4 retriever 模式 ----

def _mk_retriever(tmp_path: Path) -> tuple[Retriever, ChromaVectorStore]:
    settings = Settings(
        rag_provider="mock",
        data_dir=tmp_path / "data",
        vector_store="chroma",
        retrieval_mode="hybrid",
    )
    store = ChromaVectorStore(tmp_path / "chroma")
    store.ensure_collection(KB, 64)
    return Retriever(store, build_embedding(settings), settings), store


def test_retriever_vector_mode_matches_direct_search(tmp_path: Path) -> None:
    """vector 模式与 H1 之前直接调 vector_store.search 完全一致（回归）。"""
    retriever, store = _mk_retriever(tmp_path)
    store.add(
        KB,
        [
            ChunkToIndex(id="1:0", kb_id=KB, document_id=1, chunk_index=0, content="差旅报销制度", vector=[0.0] * 64),
            ChunkToIndex(id="1:1", kb_id=KB, document_id=1, chunk_index=1, content="年假管理制度", vector=[0.0] * 64),
        ],
    )
    direct = store.search(KB, build_embedding(retriever.settings).embed(["年假"])[0], 5)
    via = retriever.retrieve(KB, "年假", 5, mode="vector")
    assert [(h.document_id, h.chunk_index, h.content, round(h.score, 6)) for h in via] == [
        (h.document_id, h.chunk_index, h.content, round(h.score, 6)) for h in direct
    ]


def test_retriever_hybrid_recovers_lexical_only(tmp_path: Path) -> None:
    """纯向量漏掉的精确关键词（型号/代号），混合检索能捞回来（H1 核心价值）。"""
    retriever, store = _mk_retriever(tmp_path)
    store.add(
        KB,
        [
            ChunkToIndex(
                id="1:0", kb_id=KB, document_id=1, chunk_index=0,
                content="gb4806 食品接触材料标准", vector=[0.0] * 64,
            ),
            ChunkToIndex(
                id="1:1", kb_id=KB, document_id=1, chunk_index=1,
                content="日常行政管理制度", vector=[0.0] * 64,
            ),
        ],
    )
    # 向量空间里 "gb4806" 是一个整词特征，与查询 "gb"+"4806" 两个词不重合 → 纯向量 0 命中
    assert retriever.retrieve(KB, "GB 4806", 5, mode="vector") == []
    hyb_hits = retriever.retrieve(KB, "GB 4806", 5, mode="hybrid")
    assert hyb_hits and hyb_hits[0].chunk_index == 0


def test_retriever_default_mode_from_settings(tmp_path: Path) -> None:
    """retrieval_mode 配置默认（hybrid），不传 mode 走混合。"""
    retriever, store = _mk_retriever(tmp_path)
    store.add(
        KB,
        [ChunkToIndex(id="1:0", kb_id=KB, document_id=1, chunk_index=0, content="gb4806 标准", vector=[0.0] * 64)],
    )
    hits = retriever.retrieve(KB, "GB 4806", 5)
    assert hits and hits[0].chunk_index == 0
