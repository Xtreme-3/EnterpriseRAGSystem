"""重排序器：对检索结果按相关性重排，可选插槽（阶段一为 no-op）。"""
from __future__ import annotations

from app.providers.base import RerankProvider
from app.storage.vector_store import ScoredChunk


class Reranker:
    def __init__(self, provider: RerankProvider) -> None:
        self._provider = provider

    def rerank(
        self, query: str, chunks: list[ScoredChunk], top_n: int | None = None
    ) -> list[ScoredChunk]:
        if not chunks:
            return chunks
        scores = self._provider.rerank(query, [c.content for c in chunks])
        ranked = sorted(zip(chunks, scores), key=lambda item: item[1], reverse=True)
        if top_n:
            ranked = ranked[:top_n]
        return [chunk for chunk, _ in ranked]
