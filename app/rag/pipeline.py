"""RAG 编排：query → (rewrite) → retrieve → rerank → generate → 结构化答案 + 引用来源。

K1 真流式：新增 ``ask_stream()`` 事件化生成器，与 ``ask()`` 并存（后者行为零改动）。
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.core.models import Document
from app.providers.base import EmbeddingProvider, LLMProvider, RerankProvider
from app.rag.generator import Generator
from app.rag.query_rewriter import QueryRewriter, build_rewriter
from app.rag.reranker import Reranker
from app.rag.retriever import Retriever
from app.storage.db import get_db
from app.storage.vector_store import ScoredChunk, VectorStore


@dataclass
class SourceRef:
    """一条被引用的资料来源。"""

    document_id: int
    filename: str
    chunk_index: int
    content: str
    score: float


@dataclass
class RagAnswer:
    query: str
    answer: str
    sources: list[SourceRef]
    # J1 多轮：改写后的检索词（无历史/未改写时为原始 query；调试与测试用）
    rewritten_query: str = ""


class RagPipeline:
    """默认构造即装配完整链路，demo / API 均可一行使用。"""

    def __init__(
        self,
        settings: Settings | None = None,
        session_factory: sessionmaker | None = None,
        vector_store: VectorStore | None = None,
        embedding: EmbeddingProvider | None = None,
        llm: LLMProvider | None = None,
        reranker: RerankProvider | None = None,
    ) -> None:
        self.settings = settings or get_settings()

        if session_factory is None:
            from app.storage.db import init_db

            _, self.session_factory = init_db(self.settings)
        else:
            self.session_factory = session_factory

        from app.providers.factory import build_embedding, build_llm, build_reranker
        from app.storage.vector_store import build_vector_store

        # K2：向量库与 embedding 挂成公开属性，供 lifespan 装配时与摄取链路共用
        # （同一份 PG engine / 同一个 httpx 池，不重复建连接）
        self.vector_store: VectorStore = vector_store or build_vector_store(self.settings)
        self.embedding: EmbeddingProvider = embedding or build_embedding(self.settings)

        self.retriever = Retriever(self.vector_store, self.embedding, self.settings)
        self.reranker = Reranker(reranker or build_reranker(self.settings))
        self.llm = llm or build_llm(self.settings)
        # J1 多轮：查询改写（mock 默认规则策略，配真模型自动升级 LLM 改写）
        self.rewriter: QueryRewriter = build_rewriter(self.settings, self.llm)
        self.generator = Generator(self.llm)

    def ask(
        self,
        kb_id: int,
        query: str,
        top_k: int | None = None,
        mode: str | None = None,
        history: list | None = None,
    ) -> RagAnswer:
        """多轮问答：历史非空时先改写查询（只影响检索），答案 prompt 仍用原始 query。

        ``history``：含 role/content 的最近轮次；None/空 = 单轮，行为与之前完全一致。
        """
        k = top_k or self.settings.top_k
        # 检索用改写问句（消解指代），生成用原始问句（保留用户原意）
        search_query = self.rewriter.rewrite(query, history)
        hits = self.retriever.retrieve(kb_id, search_query, k, mode=mode)
        hits = self.reranker.rerank(search_query, hits, top_n=k)
        # K3：文档名必须在**生成之前**解析好——prompt 里要写出「（来源：xxx.pdf · 第 N 块）」，
        # 模型才能引用具体文档。同一份映射顺带用于来源展示，全程只查一次库。
        filenames = self._filename_map(hits)
        answer = self.generator.generate(
            query, hits, history=history, filenames=filenames
        )
        sources = self._build_sources(hits, filenames)
        return RagAnswer(query=query, answer=answer, sources=sources, rewritten_query=search_query)

    def ask_stream(
        self,
        kb_id: int,
        query: str,
        top_k: int | None = None,
        mode: str | None = None,
        history: list | None = None,
    ) -> Iterator[dict]:
        """K1 真流式：把整条链路事件化产出，供 SSE 逐事件下发。

        事件契约（``type`` 取值）：
        - ``{"type": "stage",   "stage": "rewriting"|"retrieving"|"reranking"}``
        - ``{"type": "sources", "sources": [SourceRef], "rewritten_query": str, "stage": "generating"}``
        - ``{"type": "token",   "content": str}``
        - ``{"type": "done",    "answer": str, "sources": [SourceRef], "rewritten_query": str}``

        ``sources`` 在第一个 ``token`` **之前**推出：检索与重排此时已完成，来源是确定的，
        用户不必等答案吐完就能看到引用。
        ``done`` 携带完整答案与完整 sources（非截断），调用方收到它之后才落库。
        ``ask()``（非流式）不受影响。
        """
        k = top_k or self.settings.top_k

        yield {"type": "stage", "stage": "rewriting"}
        search_query = self.rewriter.rewrite(query, history)

        yield {"type": "stage", "stage": "retrieving"}
        hits = self.retriever.retrieve(kb_id, search_query, k, mode=mode)

        yield {"type": "stage", "stage": "reranking"}
        hits = self.reranker.rerank(search_query, hits, top_n=k)

        filenames = self._filename_map(hits)
        sources = self._build_sources(hits, filenames)
        yield {
            "type": "sources",
            "sources": sources,
            "rewritten_query": search_query,
            "stage": "generating",
        }

        parts: list[str] = []
        for token in self.generator.stream(query, hits, history=history, filenames=filenames):
            parts.append(token)
            yield {"type": "token", "content": token}

        yield {
            "type": "done",
            "answer": "".join(parts),
            "sources": sources,
            "rewritten_query": search_query,
        }

    def _build_sources(
        self, hits: list[ScoredChunk], filenames: dict[int, str] | None = None
    ) -> list[SourceRef]:
        """检索命中 → 引用来源（ask / ask_stream 共用，避免两处漂移）。

        文档名由调用方传入（K3）：生成 prompt 与来源展示用的是**同一份**映射，
        因此全程只查一次库，也不存在"prompt 里有名字、来源列表里没有"的错位。
        """
        fmap = filenames or {}
        return [
            SourceRef(
                document_id=c.document_id,
                filename=fmap.get(c.document_id, f"doc_{c.document_id}"),
                chunk_index=c.chunk_index,
                content=c.content,
                score=c.score,
            )
            for c in hits
        ]

    def _filename_map(self, hits: list[ScoredChunk]) -> dict[int, str]:
        """命中切片 → {document_id: 文件名}。

        K3 后必须在**生成之前**调用：prompt 里要渲染「（来源：文件名 · 第 N 块）」，
        模型才能引用具体文档而不是笼统的"资料显示"。
        """
        doc_ids = {c.document_id for c in hits}
        if not doc_ids:
            return {}
        with get_db(self.session_factory) as db:
            docs = db.query(Document).filter(Document.id.in_(doc_ids)).all()
        return {d.id: d.filename for d in docs}
