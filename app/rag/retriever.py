"""检索器：query → 按模式召回（纯向量 / 向量+关键词混合）→ top-k 命中。"""
from __future__ import annotations

from app.config import Settings, get_settings
from app.providers.base import EmbeddingProvider
from app.rag.hybrid import fuse_hybrid
from app.storage.vector_store import ScoredChunk, VectorStore


class Retriever:
    def __init__(
        self,
        vector_store: VectorStore,
        embedding: EmbeddingProvider,
        settings: Settings | None = None,
    ) -> None:
        self.vector_store = vector_store
        self.embedding = embedding
        self.settings = settings or get_settings()

    def retrieve(
        self, kb_id: int, query: str, top_k: int | None = None, mode: str | None = None
    ) -> list[ScoredChunk]:
        """召回切片。mode：vector | hybrid（None 用配置默认）。

        - ``vector``：纯向量相似度（H1 之前行为，回归不变）
        - ``hybrid``：向量 + 关键词全文双路召回，加权归一化融合（H1）
        """
        k = top_k or self.settings.top_k
        mode = mode or self.settings.retrieval_mode
        if mode == "vector":
            return self._retrieve_vector(kb_id, query, k)
        return self._retrieve_hybrid(kb_id, query, k)

    def _retrieve_vector(self, kb_id: int, query: str, top_k: int) -> list[ScoredChunk]:
        query_vec = self.embedding.embed([query])[0]
        return self.vector_store.search(kb_id, query_vec, top_k)

    def _retrieve_hybrid(self, kb_id: int, query: str, top_k: int) -> list[ScoredChunk]:
        query_vec = self.embedding.embed([query])[0]
        vector_hits = self.vector_store.search(kb_id, query_vec, top_k)
        lexical_hits = self.vector_store.search_lexical(kb_id, query, top_k)
        return fuse_hybrid(vector_hits, lexical_hits, top_k)
