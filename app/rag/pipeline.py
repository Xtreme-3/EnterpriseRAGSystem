"""RAG 编排：query → (rewrite) → retrieve → rerank → generate → 结构化答案 + 引用来源。

K1 真流式：新增 ``ask_stream()`` 事件化生成器，与 ``ask()`` 并存（后者行为零改动）。
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field

from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.core.models import Document
from app.providers.base import EmbeddingProvider, LLMProvider, RerankProvider
from app.rag.generator import Generator, citation_issues
from app.rag.query_cache import QueryCache
from app.rag.query_rewriter import QueryRewriter, build_rewriter
from app.rag.reranker import Reranker
from app.rag.retriever import Retriever
from app.storage.db import get_db
from app.storage.vector_store import ScoredChunk, VectorStore

logger = logging.getLogger(__name__)


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
    # K3 批 2：答案引用了不存在的来源编号（越界）；空列表 = 引用全部有效
    citation_issues: list[int] = field(default_factory=list)
    # K6：本次回答是否来自问答缓存（AskResponse / done 事件透传，前端展示「缓存」徽标）
    cache_hit: bool = False


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
        cache: QueryCache | None = None,
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
        # K7：全局未启用重排、但单次请求要求重排时，按需构建的强制重排器（懒建、只建一次）
        self._forced_reranker: Reranker | None = None
        # K6：问答缓存（精确 + 语义两级，kb 级失效）——随管线装配一次，共用 session_factory
        self.cache = cache or QueryCache(self.settings, self.session_factory, self.embedding)

    def ask(
        self,
        kb_id: int,
        query: str,
        top_k: int | None = None,
        mode: str | None = None,
        history: list | None = None,
        rerank: bool | None = None,
        model: str | None = None,
    ) -> RagAnswer:
        """多轮问答：历史非空时先改写查询（只影响检索），答案 prompt 仍用原始 query。

        ``history``：含 role/content 的最近轮次；None/空 = 单轮，行为与之前完全一致。
        ``rerank`` / ``model``（K7）：对话页参数，语义见 ``_maybe_rerank`` 与 ``Generator``。
        """
        k = top_k or self.settings.top_k
        # K6：单轮请求先查缓存（多轮 history 非空一律不查不写）
        cached = self._cache_lookup(kb_id, query, mode, k, history, model=model)
        if cached is not None:
            return cached
        # 检索用改写问句（消解指代），生成用原始问句（保留用户原意）
        search_query = self.rewriter.rewrite(query, history)
        hits = self.retriever.retrieve(kb_id, search_query, k, mode=mode)
        hits = self._maybe_rerank(search_query, hits, k, rerank)
        # K3：文档名必须在**生成之前**解析好——prompt 里要写出「（来源：xxx.pdf · 第 N 块）」，
        # 模型才能引用具体文档。同一份映射顺带用于来源展示，全程只查一次库。
        filenames = self._filename_map(hits)
        answer = self.generator.generate(
            query, hits, history=history, filenames=filenames, model=model
        )
        sources = self._build_sources(hits, filenames)
        issues = citation_issues(answer, len(sources))
        self._log_citation_issues(kb_id, issues, len(sources))
        result = RagAnswer(
            query=query,
            answer=answer,
            sources=sources,
            rewritten_query=search_query,
            citation_issues=issues,
        )
        self._cache_put(
            kb_id, query, mode, k, history,
            answer=result.answer, sources=result.sources,
            rewritten_query=result.rewritten_query,
            citation_issues=result.citation_issues, model=model,
        )
        return result

    def ask_stream(
        self,
        kb_id: int,
        query: str,
        top_k: int | None = None,
        mode: str | None = None,
        history: list | None = None,
        rerank: bool | None = None,
        model: str | None = None,
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
        ``ask()``（非流式）不受影响。``rerank`` / ``model``（K7）与 ``ask()`` 同义。
        """
        k = top_k or self.settings.top_k

        # K6：单轮请求命中缓存时直接重放 —— sources 照常先发（契约不变），
        # 答案按段重放成 token 事件，done 带 cache_hit；落库由调用方照常进行。
        cached = self._cache_lookup(kb_id, query, mode, k, history, model=model)
        if cached is not None:
            yield {
                "type": "sources",
                "sources": cached.sources,
                "rewritten_query": cached.rewritten_query,
                "stage": "generating",
                "cache_hit": True,
            }
            step = 24
            for i in range(0, len(cached.answer), step):
                yield {"type": "token", "content": cached.answer[i : i + step]}
            yield {
                "type": "done",
                "answer": cached.answer,
                "sources": cached.sources,
                "rewritten_query": cached.rewritten_query,
                "citation_issues": cached.citation_issues,
                "cache_hit": True,
            }
            return

        yield {"type": "stage", "stage": "rewriting"}
        search_query = self.rewriter.rewrite(query, history)

        yield {"type": "stage", "stage": "retrieving"}
        hits = self.retriever.retrieve(kb_id, search_query, k, mode=mode)

        yield {"type": "stage", "stage": "reranking"}
        hits = self._maybe_rerank(search_query, hits, k, rerank)

        filenames = self._filename_map(hits)
        sources = self._build_sources(hits, filenames)
        yield {
            "type": "sources",
            "sources": sources,
            "rewritten_query": search_query,
            "stage": "generating",
        }

        parts: list[str] = []
        for token in self.generator.stream(
            query, hits, history=history, filenames=filenames, model=model
        ):
            parts.append(token)
            yield {"type": "token", "content": token}

        answer = "".join(parts)
        # 流式下答案在 done 前才拼完，越界引用只能在这里校验（与 ask() 同一函数，不漂移）
        issues = citation_issues(answer, len(sources))
        self._log_citation_issues(kb_id, issues, len(sources))
        self._cache_put(
            kb_id, query, mode, k, history,
            answer=answer, sources=sources,
            rewritten_query=search_query,
            citation_issues=issues, model=model,
        )
        yield {
            "type": "done",
            "answer": answer,
            "sources": sources,
            "rewritten_query": search_query,
            "citation_issues": issues,
            "cache_hit": False,
        }

    @staticmethod
    def _log_citation_issues(kb_id: int, issues: list[int], source_count: int) -> None:
        """越界引用 = 模型编了不存在的来源。界面表现为点不开的假引用，
        因此服务端必须留痕（K8 约定：可观测的东西不能只躺在响应体里）。"""
        if issues:
            logger.warning(
                "答案引用了不存在的来源编号 kb_id=%s 越界编号=%s 实际来源数=%s",
                kb_id, issues, source_count,
            )

    def _maybe_rerank(
        self, query: str, hits: list[ScoredChunk], k: int, rerank: bool | None
    ) -> list[ScoredChunk]:
        """按请求 rerank 开关决定是否重排（K7，语义与质检台一致）。

        - ``None``：跟随 ``Settings.rerank``（默认）。全局关闭时等价于原来的
          NoopRerank 恒等路径 —— 排序与分数完全不变，只是省掉一次恒等调用；
        - ``False``：本次跳过重排（全局开启时也可按问临时关闭）；
        - ``True``：强制重排。全局未启用时按需构建强制重排器并缓存；
          构建失败（如重排供应商缺 Key）按原样抛出，由路由层转 502 / 流内 error。
        """
        if not hits:
            return hits
        effective = rerank if rerank is not None else self.settings.rerank
        if not effective:
            return hits
        reranker = self.reranker if self.settings.rerank else self._get_forced_reranker()
        return reranker.rerank(query, hits, top_n=k)

    def _get_forced_reranker(self) -> Reranker:
        """全局未启用重排但本次请求要求重排：按需构建并缓存（K2：只建一次）。"""
        if self._forced_reranker is None:
            from app.providers.factory import build_reranker

            try:
                self._forced_reranker = Reranker(
                    build_reranker(self.settings.model_copy(update={"rerank": True}))
                )
            except Exception:
                # K8 约定：新增的 except 必须留痕。缺 Key 这类配置问题在这里暴露。
                logger.exception("按请求启用重排失败（检查重排供应商配置）")
                raise
        return self._forced_reranker

    def _cache_lookup(
        self,
        kb_id: int,
        query: str,
        mode: str | None,
        k: int,
        history: list | None,
        model: str | None = None,
    ) -> RagAnswer | None:
        """K6：单轮请求查缓存；多轮（history 非空）或缓存关闭时不查不写。

        缓存的任何故障都降级为「未命中」并留痕 —— 绝不影响问答主链路。
        ``model`` 归一为服务端默认模型名后参与缓存键：切模型必须换缓存，
        否则 K7 的「快慢自选」会被缓存静默吞掉。
        """
        if history or not self.cache.enabled:
            return None
        try:
            hit = self.cache.lookup(
                kb_id,
                query,
                mode or self.settings.retrieval_mode,
                k,
                model or self.settings.llm_model,
            )
        except Exception:
            logger.warning("问答缓存读取失败，按未命中处理 kb_id=%s", kb_id, exc_info=True)
            return None
        if hit is None:
            return None
        return RagAnswer(
            query=query,
            answer=hit.answer,
            sources=[SourceRef(**s) for s in hit.sources],
            rewritten_query=hit.rewritten_query,
            citation_issues=hit.citation_issues,
            cache_hit=True,
        )

    def _cache_put(
        self,
        kb_id: int,
        query: str,
        mode: str | None,
        k: int,
        history: list | None,
        *,
        answer: str,
        sources: list[SourceRef],
        rewritten_query: str,
        citation_issues: list[int],
        model: str | None,
    ) -> None:
        """K6：问答成功后写缓存（多轮 / 缓存关闭时不写；写失败只留痕不报错）。"""
        if history or not self.cache.enabled:
            return
        try:
            self.cache.store(
                kb_id=kb_id,
                query=query,
                mode=mode or self.settings.retrieval_mode,
                top_k=k,
                model=model or self.settings.llm_model,
                answer=answer,
                sources=[
                    {
                        "document_id": s.document_id,
                        "filename": s.filename,
                        "chunk_index": s.chunk_index,
                        "content": s.content,
                        "score": s.score,
                    }
                    for s in sources
                ],
                rewritten_query=rewritten_query,
                citation_issues=citation_issues,
            )
        except Exception:
            logger.warning("问答缓存写入失败（不影响本次回答）kb_id=%s", kb_id, exc_info=True)

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
