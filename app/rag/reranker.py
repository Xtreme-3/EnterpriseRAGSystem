"""重排序器：对检索结果按相关性重排，可选插槽（Noop / Mock / 真实模型）。"""
from __future__ import annotations

from dataclasses import replace

from app.providers.base import RerankProvider
from app.storage.vector_store import ScoredChunk


class Reranker:
    def __init__(self, provider: RerankProvider) -> None:
        self._provider = provider

    def rerank(
        self, query: str, chunks: list[ScoredChunk], top_n: int | None = None
    ) -> list[ScoredChunk]:
        """按重排分降序返回前 top_n 条，并把命中 score 更新为重排分（I1）。

        Noop 实现返回原检索分 → 排序与分数完全不变（回归路径）。
        """
        if not chunks:
            return []
        texts = [c.content for c in chunks]
        scores = [c.score for c in chunks]
        reranked = self._provider.rerank(query, texts, scores)
        ranked = sorted(zip(chunks, reranked), key=lambda item: item[1], reverse=True)
        if top_n:
            ranked = ranked[:top_n]
        return [replace(chunk, score=score) for chunk, score in ranked]
