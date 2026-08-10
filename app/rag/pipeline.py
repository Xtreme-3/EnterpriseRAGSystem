"""RAG 编排：query → retrieve → rerank → generate → 结构化答案 + 引用来源。"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.core.models import Document
from app.providers.base import EmbeddingProvider, LLMProvider, RerankProvider
from app.rag.generator import Generator
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
        self.generator = Generator(llm or build_llm(self.settings))

    def ask(self, kb_id: int, query: str, top_k: int | None = None, mode: str | None = None) -> RagAnswer:
        k = top_k or self.settings.top_k
        hits = self.retriever.retrieve(kb_id, query, k, mode=mode)
        hits = self.reranker.rerank(query, hits, top_n=k)
        answer = self.generator.generate(query, hits)
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
        return RagAnswer(query=query, answer=answer, sources=sources)

    def _attach_filenames(self, sources: list[SourceRef]) -> None:
        doc_ids = {s.document_id for s in sources}
        if not doc_ids:
            return
        with get_db(self.session_factory) as db:
            docs = db.query(Document).filter(Document.id.in_(doc_ids)).all()
        filename_map = {d.id: d.filename for d in docs}
        for s in sources:
            s.filename = filename_map.get(s.document_id, f"doc_{s.document_id}")
