"""按配置装配模型供应商。业务代码只调用本模块，不直接 new 具体实现。"""
from __future__ import annotations

import httpx

from app.config import Settings, get_settings
from app.providers.base import EmbeddingProvider, LLMProvider, RerankProvider
from app.providers.mock import MockEmbedding, MockLLM
from app.providers.openai_compat import OpenAICompatEmbedding, OpenAICompatLLM

_RERANK_TIMEOUT = 30.0


class NoopRerank(RerankProvider):
    """不重排：原样返回检索分，保持检索排序与分数不变（默认/回归路径）。"""

    def rerank(self, query: str, texts: list[str], scores: list[float]) -> list[float]:
        return list(scores)


class MockRerank(RerankProvider):
    """离线重排（I1）：query 分词后按与候选的词重叠率打分，确定性、无网络。

    复用 H1 的 tokenize 保证 query 与入库侧词空间一致。无可用检索词时回退原分。
    仅离线/开发演示用——纯词重叠没有语义，真实效果以 OpenAICompatRerank 为准。
    """

    def rerank(self, query: str, texts: list[str], scores: list[float]) -> list[float]:
        from app.rag.hybrid import tokenize

        terms = tokenize(query)
        if not terms:
            return list(scores)
        n = len(terms)
        out: list[float] = []
        for t in texts:
            lower = t.lower()
            out.append(sum(1 for term in terms if term in lower) / n)
        return out


class OpenAICompatRerank(RerankProvider):
    """真实重排：POST {base_url}/rerank（OpenAI 兼容模式，DashScope gte-rerank）。

    openai SDK 无 rerank 方法，用 httpx 直连。响应解析抽成类方法便于离线单测。
    """

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    def rerank(self, query: str, texts: list[str], scores: list[float]) -> list[float]:
        if not texts:
            return []
        resp = httpx.post(
            f"{self._base_url}/rerank",
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self._model, "query": query, "documents": texts},
            timeout=_RERANK_TIMEOUT,
        )
        resp.raise_for_status()
        return self._parse(resp.json(), n=len(texts))

    @staticmethod
    def _parse(payload: dict, *, n: int) -> list[float]:
        """按 results[].index 对齐返回 n 条重排分；缺 index/字段兜底，不 500。"""
        results = payload.get("results") or []
        scores: list[float] = [0.0] * n
        for r in results:
            idx = int(r.get("index", 0))
            if not (0 <= idx < n):
                continue
            raw = r.get("relevance_score", r.get("score", 0.0))
            scores[idx] = float(raw)
        return scores


def _resolve_endpoint(settings: Settings) -> tuple[str, str]:
    """按 RAG_PROVIDER 返回 (base_url, api_key)，key 为空时给出明确提示。"""
    if settings.rag_provider == "dashscope":
        base_url, api_key = settings.dashscope_base_url, settings.dashscope_api_key
    elif settings.rag_provider == "zhipu":
        base_url, api_key = settings.zhipu_base_url, settings.zhipu_api_key
    else:
        raise ValueError(f"不支持的非 OpenAI 兼容供应商: {settings.rag_provider}")

    if not api_key:
        raise ValueError(
            f"供应商 {settings.rag_provider} 未配置 API Key。"
            f"请复制 .env.example 为 .env，填写 "
            f"{settings.rag_provider.upper()}_API_KEY 后重试。"
        )
    return base_url, api_key


def build_embedding(settings: Settings | None = None) -> EmbeddingProvider:
    s = settings or get_settings()
    if s.rag_provider == "mock":
        return MockEmbedding()
    base_url, api_key = _resolve_endpoint(s)
    return OpenAICompatEmbedding(
        base_url=base_url, api_key=api_key, model=s.embedding_model, dim=s.embedding_dim
    )


def build_llm(settings: Settings | None = None) -> LLMProvider:
    s = settings or get_settings()
    if s.rag_provider == "mock":
        return MockLLM()
    base_url, api_key = _resolve_endpoint(s)
    return OpenAICompatLLM(base_url=base_url, api_key=api_key, model=s.llm_model)


def build_reranker(settings: Settings | None = None) -> RerankProvider:
    """按配置装配重排器（I1）。

    - ``rerank=False``（默认）→ NoopRerank：排序与分数完全不变（回归 0 影响）
    - ``mock`` 供应商 → MockRerank：离线词重叠重排，确定性可测
    - 真实供应商 → OpenAICompatRerank（DashScope gte-rerank 等，走 {base_url}/rerank）
    """
    s = settings or get_settings()
    if not s.rerank:
        return NoopRerank()
    if s.rag_provider == "mock":
        return MockRerank()
    base_url, api_key = _resolve_endpoint(s)
    return OpenAICompatRerank(base_url=base_url, api_key=api_key, model=s.rerank_model)
