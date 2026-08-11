"""RAG 编排：query → (rewrite) → retrieve → rerank → generate → 结构化答案 + 引用来源。"""
from __future__ import annotations

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

        self.retriever = Retriever(
            vector_store or build_vector_store(self.settings),
            embedding or build_embedding(self.settings),
            self.settings,
        )
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
        answer = self.generator.generate(query, hits, history=history)
        sources = [
            SourceRef(
                document_id=c.document_id,
                filename="",
                chunk_index=c.chunk_index,
                content=c.content,
                score=c.score,
            )
            for c in hits
        ]
        self._attach_filenames(sources)
        return RagAnswer(query=query, answer=answer, sources=sources, rewritten_query=search_query)

    def _attach_filenames(self, sources: list[SourceRef]) -> None:
        doc_ids = {s.document_id for s in sources}
        if not doc_ids:
            return
        with get_db(self.session_factory) as db:
            docs = db.query(Document).filter(Document.id.in_(doc_ids)).all()
        filename_map = {d.id: d.filename for d in docs}
        for s in sources:
            s.filename = filename_map.get(s.document_id, f"doc_{s.document_id}")
