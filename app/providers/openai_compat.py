"""OpenAI 兼容接口实现：通义千问(DashScope) 与 智谱 GLM 均提供兼容 base_url。

用官方 openai SDK 指向各家 base_url，即可复用同一套代码，无需引入各家 SDK。
"""
from __future__ import annotations

from collections.abc import Iterator

from openai import OpenAI

from app.providers.base import EmbeddingProvider, LLMProvider

# 各家 embedding 接口的单次请求条数上限（保守取值，循环分批）
_EMBED_BATCH = 16

# K2：显式超时与重试。SDK 默认读超时为 600s 且重试次数不受限 —— 真模型挂住时
# 请求会一直等下去（前端表现为"永远转圈"）。这里收敛为可预期的上限。
_TIMEOUT_SECONDS = 60.0
_MAX_RETRIES = 2


def _make_client(*, base_url: str, api_key: str) -> OpenAI:
    """统一构造 OpenAI 客户端：显式 timeout + 有界 max_retries（K2）。"""
    return OpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=_TIMEOUT_SECONDS,
        max_retries=_MAX_RETRIES,
    )


class OpenAICompatLLM(LLMProvider):
    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self._client = _make_client(base_url=base_url, api_key=api_key)
        self._model = model

    def _params(self, prompt: str, max_tokens: int, *, stream: bool) -> dict:
        """两种模式的公共请求参数，避免 complete/stream 漂移。"""
        return {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "stream": stream,
        }

    def complete(self, prompt: str, *, max_tokens: int = 1024) -> str:
        resp = self._client.chat.completions.create(
            **self._params(prompt, max_tokens, stream=False)
        )
        return resp.choices[0].message.content or ""

    def stream(self, prompt: str, *, max_tokens: int = 1024) -> Iterator[str]:
        """K1 真流式：逐 delta.content 产出增量文本。

        只取 ``delta.content``：推理模型（如 qwen3.8-flash）会先产 ``reasoning_content``，
        那段思考内容不下发给用户，由前端"正在组织答案…"阶段兜住这段静默期。
        首包/末包可能出现 content 为 None 或空串的 chunk，统一跳过。
        """
        for chunk in self._client.chat.completions.create(
            **self._params(prompt, max_tokens, stream=True)
        ):
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            content = getattr(getattr(choices[0], "delta", None), "content", None)
            if content:
                yield content


class OpenAICompatEmbedding(EmbeddingProvider):
    def __init__(self, *, base_url: str, api_key: str, model: str, dim: int) -> None:
        self._client = _make_client(base_url=base_url, api_key=api_key)
        self._model = model
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH):
            batch = texts[i : i + _EMBED_BATCH]
            resp = self._client.embeddings.create(model=self._model, input=batch)
            ordered = sorted(resp.data, key=lambda d: d.index)
            vectors.extend(item.embedding for item in ordered)
        return vectors
