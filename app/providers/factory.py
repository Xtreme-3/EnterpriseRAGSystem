"""按配置装配模型供应商。业务代码只调用本模块，不直接 new 具体实现。"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from app.config import Settings, get_settings
from app.providers.base import EmbeddingProvider, LLMProvider, RerankProvider
from app.providers.mock import MockEmbedding, MockLLM
from app.providers.openai_compat import OpenAICompatEmbedding, OpenAICompatLLM

logger = logging.getLogger(__name__)

_RERANK_TIMEOUT = 30.0

# 百炼原生 rerank 端点。**不在** compatible-mode 下：实测 {base_url}/rerank 返回 404。
_DASHSCOPE_RERANK_PATH = "/api/v1/services/rerank/text-rerank/text-rerank"


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
        filled = 0
        for r in results:
            idx = int(r.get("index", 0))
            if not (0 <= idx < n):
                continue
            raw = r.get("relevance_score", r.get("score", 0.0))
            scores[idx] = float(raw)
            filled += 1
        _warn_incomplete_rerank(filled, n)
        return scores


class DashScopeRerank(RerankProvider):
    """百炼官方重排：走 DashScope **原生**端点，不是 OpenAI 兼容模式。

    为什么不复用 ``OpenAICompatRerank``（K0 实测结论）：

    - 百炼的 rerank 没有 OpenAI 兼容入口，``{base_url}/rerank`` 直接 **404**；
    - 原生端点路径与兼容模式不同（``/api/v1/services/rerank/...``，站点根下而非 ``/v1`` 下）；
    - 原生入参是 ``{"input": {"query", "documents"}}``，且 results 嵌在 ``output`` 下。

    实测模型：``gte-rerank-v2`` / ``qwen3-rerank`` 均可用（各有 100 万 token 免费额度）。
    """

    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        # base_url 配的是 compatible-mode 端点，这里退到站点根再拼原生路径
        parsed = urlparse(base_url)
        self._endpoint = f"{parsed.scheme}://{parsed.netloc}{_DASHSCOPE_RERANK_PATH}"
        self._api_key = api_key
        self._model = model

    def rerank(self, query: str, texts: list[str], scores: list[float]) -> list[float]:
        if not texts:
            return []
        resp = httpx.post(
            self._endpoint,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "input": {"query": query, "documents": texts},
                # top_n 必须给足候选数：小于候选数时只回前 N 条，其余候选拿不到分
                "parameters": {"return_documents": False, "top_n": len(texts)},
            },
            timeout=_RERANK_TIMEOUT,
        )
        resp.raise_for_status()
        return self._parse(resp.json(), n=len(texts))

    @staticmethod
    def _parse(payload: dict, *, n: int) -> list[float]:
        """按 ``output.results[].index`` 对齐返回 n 条重排分；缺字段兜底，不 500。"""
        results = (payload.get("output") or {}).get("results") or []
        out: list[float] = [0.0] * n
        filled = 0
        for r in results:
            idx = int(r.get("index", 0))
            if not (0 <= idx < n):
                continue
            out[idx] = float(r.get("relevance_score", r.get("score", 0.0)))
            filled += 1
        _warn_incomplete_rerank(filled, n)
        return out


def _warn_incomplete_rerank(filled: int, n: int) -> None:
    """重排结果不满时的告警（K8-5）。

    缺字段/缺条目的候选会被静默填 0，于是它们被排到最末尾 —— 用户感受到的只是
    "搜得不准"，服务端没有任何痕迹。这正是"检索质量下降却查不出来"的来源。
    """
    if filled >= n:
        return
    logger.warning(
        "重排服务返回结果不完整：期望 %s 条，实得 %s 条；缺失候选的重排分按 0 处理，"
        "这些候选会被排到末尾（不报错，但排序质量下降）。",
        n,
        filled,
    )


def _resolve_endpoint(settings: Settings, provider: str | None = None) -> tuple[str, str]:
    """按供应商名返回 (base_url, api_key)，key 为空时给出明确提示。

    ``provider`` 缺省时用 settings.rag_provider；插槽级覆盖（embedding_provider /
    llm_provider / rerank_provider）由各 build_* 自行决定传入值。

    供应商名互不复用：``dashscope`` 在本项目里历史指向第三方中转站，
    ``bailian`` 才是阿里云百炼官方端点，两者 base_url 不同，故并列存在。
    """
    p = provider or settings.rag_provider
    if p == "dashscope":
        base_url, api_key = settings.dashscope_base_url, settings.dashscope_api_key
    elif p == "bailian":
        base_url, api_key = settings.bailian_base_url, settings.bailian_api_key
    elif p == "zhipu":
        base_url, api_key = settings.zhipu_base_url, settings.zhipu_api_key
    else:
        raise ValueError(f"不支持的非 OpenAI 兼容供应商: {p}")

    if not api_key:
        raise ValueError(
            f"供应商 {p} 未配置 API Key。"
            f"请复制 .env.example 为 .env，填写 "
            f"{p.upper()}_API_KEY 后重试。"
        )
    return base_url, api_key


def build_embedding(settings: Settings | None = None) -> EmbeddingProvider:
    s = settings or get_settings()
    p = s.embedding_provider or s.rag_provider
    if p == "mock":
        return MockEmbedding()
    base_url, api_key = _resolve_endpoint(s, p)
    return OpenAICompatEmbedding(
        base_url=base_url, api_key=api_key, model=s.embedding_model, dim=s.embedding_dim
    )


def build_llm(settings: Settings | None = None) -> LLMProvider:
    s = settings or get_settings()
    p = s.llm_provider or s.rag_provider
    if p == "mock":
        return MockLLM()
    base_url, api_key = _resolve_endpoint(s, p)
    return OpenAICompatLLM(
        base_url=base_url,
        api_key=api_key,
        model=s.llm_model,
        temperature=s.llm_temperature,
        max_tokens=s.llm_max_tokens,
        enable_thinking=s.thinking_flag,
    )


def build_reranker(settings: Settings | None = None) -> RerankProvider:
    """按配置装配重排器（I1）。

    - ``rerank=False``（默认）→ NoopRerank：排序与分数完全不变（回归 0 影响）
    - ``mock`` 供应商 → MockRerank：离线词重叠重排，确定性可测
    - ``bailian`` → DashScopeRerank：百炼**原生**端点（compatible 下没有 rerank，会 404）
    - 其他真实供应商 → OpenAICompatRerank（走 {base_url}/rerank）

    供应商取 ``rerank_provider`` → ``llm_provider`` → ``rag_provider``（K0）。
    加 ``rerank_provider`` 是因为 embedding 与 LLM 可能分属不同家中转，
    重排该跟向量空间更近的那家走，不能硬绑在 LLM 槽位上。
    """
    s = settings or get_settings()
    if not s.rerank:
        return NoopRerank()
    p = s.rerank_provider or s.llm_provider or s.rag_provider
    if p == "mock":
        return MockRerank()
    base_url, api_key = _resolve_endpoint(s, p)
    if p == "bailian":
        return DashScopeRerank(base_url=base_url, api_key=api_key, model=s.rerank_model)
    return OpenAICompatRerank(base_url=base_url, api_key=api_key, model=s.rerank_model)
