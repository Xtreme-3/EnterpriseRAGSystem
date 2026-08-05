"""按配置装配模型供应商。业务代码只调用本模块，不直接 new 具体实现。"""
from __future__ import annotations

from app.config import Settings, get_settings
from app.providers.base import EmbeddingProvider, LLMProvider, RerankProvider
from app.providers.mock import MockEmbedding, MockLLM
from app.providers.openai_compat import OpenAICompatEmbedding, OpenAICompatLLM


class NoopRerank(RerankProvider):
    """阶段一不接重排序：按原检索顺序打分（首条最高），保持检索排序稳定。"""

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        n = len(texts)
        return [float(n - i) for i in range(n)]


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
    # 阶段一：仅返回 no-op。阶段二在此接入 gte-rerank / 智谱 rerank。
    return NoopRerank()
