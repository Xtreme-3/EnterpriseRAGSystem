"""检索器：query → embedding → 向量库 top-k 命中。"""
from __future__ import annotations

from app.config import Settings, get_settings
from app.providers.base import EmbeddingProvider
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

    def retrieve(self, kb_id: int, query: str, top_k: int | None = None) -> list[ScoredChunk]:
        query_vec = self.embedding.embed([query])[0]
        return self.vector_store.search(kb_id, query_vec, top_k or self.settings.top_k)
