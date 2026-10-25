"""检索器：query → 按模式召回（纯向量 / 向量+关键词混合）→ top-k 命中。"""
from __future__ import annotations

from app.config import Settings, get_settings
from app.providers.base import EmbeddingProvider
from app.rag.hybrid import LEXICAL_EVIDENCE_MIN_OVERLAP, fuse_hybrid, lexical_overlap
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

    def retrieve_gated(
        self, kb_id: int, query: str, top_k: int | None = None, mode: str | None = None
    ) -> tuple[list[ScoredChunk], bool]:
        """检索 + L1 双阈值拒答门：返回 ``(命中, 是否判定为库外)``。

        门只在 hybrid 模式下可能触发（L1 定案：vector 模式没有关键词路这条证据，
        强行启用会误杀库内最弱问）。**组合证据**，两条同时成立才拒答：

            ① 向量候选 raw 余弦最高的一条也 < ``answer_threshold``（没有强相关切片）
            ② 关键词路证据不足（``lexical_overlap`` < ``LEXICAL_EVIDENCE_MIN_OVERLAP``）
            → 库外，调用方应直接标准拒答、不调 LLM。

        为什么条件②不是「关键词 0 命中」：``tokenize`` 是中文重叠 2-gram，
        「美国站的退货窗口是多少天？」会切出「美国」，只要库内任何切片含「美国」
        关键词路就非空 —— 实测双库 6 条库外探针**全部**因此漏网，门形同虚设
        （2026-10-07 实测，见 scripts/measure_refusal_gate.py）。改成看**覆盖度**
        后，库外最多覆盖 1 个词、库内最少 2 个，才真正分开。细节见 hybrid.py。

        ``fuse_hybrid`` 在关键词路为空时原样返回向量路 raw 分，因此门限比较的是与
        `measure_similarity_margin.py` 同尺度的 raw 余弦，不是融合分。
        ``answer_threshold <= 0`` = 门关闭，行为完全回退。
        ``retrieve()`` 本身不受影响（inspect 质检台等调用方零波及）。
        """
        k = top_k or self.settings.top_k
        mode = mode or self.settings.retrieval_mode
        threshold = self.settings.answer_threshold
        if mode != "hybrid" or threshold <= 0:
            return self.retrieve(kb_id, query, k, mode=mode), False
        query_vec = self.embedding.embed([query])[0]
        vector_hits = self.vector_store.search(kb_id, query_vec, k)
        lexical_hits = self.vector_store.search_lexical(kb_id, query, k)
        refuse = (
            bool(vector_hits)
            and max(h.score for h in vector_hits) < threshold
            and lexical_overlap(query, lexical_hits) < LEXICAL_EVIDENCE_MIN_OVERLAP
        )
        return fuse_hybrid(vector_hits, lexical_hits, k), refuse

    def _retrieve_vector(self, kb_id: int, query: str, top_k: int) -> list[ScoredChunk]:
        query_vec = self.embedding.embed([query])[0]
        return self.vector_store.search(kb_id, query_vec, top_k)

    def _retrieve_hybrid(self, kb_id: int, query: str, top_k: int) -> list[ScoredChunk]:
        query_vec = self.embedding.embed([query])[0]
        vector_hits = self.vector_store.search(kb_id, query_vec, top_k)
        lexical_hits = self.vector_store.search_lexical(kb_id, query, top_k)
        return fuse_hybrid(vector_hits, lexical_hits, top_k)
