"""OpenAI 兼容接口实现：通义千问(DashScope) 与 智谱 GLM 均提供兼容 base_url。

用官方 openai SDK 指向各家 base_url，即可复用同一套代码，无需引入各家 SDK。
"""
from __future__ import annotations

from openai import OpenAI

from app.providers.base import EmbeddingProvider, LLMProvider

# 各家 embedding 接口的单次请求条数上限（保守取值，循环分批）
_EMBED_BATCH = 16


class OpenAICompatLLM(LLMProvider):
    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self._client = OpenAI(base_url=base_url, api_key=api_key)
        self._model = model

    def complete(self, prompt: str, *, max_tokens: int = 1024) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.2,
        )
        return resp.choices[0].message.content or ""


class OpenAICompatEmbedding(EmbeddingProvider):
    def __init__(self, *, base_url: str, api_key: str, model: str, dim: int) -> None:
        self._client = OpenAI(base_url=base_url, api_key=api_key)
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
