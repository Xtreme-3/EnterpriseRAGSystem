"""OpenAI 兼容接口实现：通义千问(DashScope) 与 智谱 GLM 均提供兼容 base_url。

用官方 openai SDK 指向各家 base_url，即可复用同一套代码，无需引入各家 SDK。
"""
from __future__ import annotations

import logging
from collections.abc import Iterator

from openai import OpenAI

from app.providers.base import EmbeddingProvider, LLMProvider

logger = logging.getLogger(__name__)

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
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> None:
        self._client = _make_client(base_url=base_url, api_key=api_key)
        self._model = model
        # 生成参数由配置注入（K3），不再硬编码在 _params 里
        self._temperature = temperature
        self._max_tokens = max_tokens

    @staticmethod
    def _messages(prompt: str, system: str | None) -> list[dict]:
        """K3：system 作为首条 system 消息；传 None 退化单条 user（与改动前一致）。"""
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return messages

    def _params(
        self, prompt: str, max_tokens: int | None, system: str | None, *, stream: bool
    ) -> dict:
        """两种模式的公共请求参数，避免 complete/stream 漂移。"""
        return {
            "model": self._model,
            "messages": self._messages(prompt, system),
            "max_tokens": self._max_tokens if max_tokens is None else max_tokens,
            "temperature": self._temperature,
            "stream": stream,
        }

    def complete(
        self, prompt: str, *, max_tokens: int | None = None, system: str | None = None
    ) -> str:
        resp = self._client.chat.completions.create(
            **self._params(prompt, max_tokens, system, stream=False)
        )
        choice = resp.choices[0]
        effective = self._max_tokens if max_tokens is None else max_tokens
        self._warn_if_truncated(choice, resp, effective)
        return choice.message.content or ""

    def _warn_if_truncated(self, choice: object, resp: object, max_tokens: int) -> None:
        """答案被 max_tokens 砍断时留下告警（K3）。

        推理模型（qwen3.8-flash 等）的 ``reasoning_tokens`` 与正文**共用**同一份
        ``max_tokens`` 预算，预算不够时正文会被截在句子中间、甚至一个字都没有，
        而 HTTP 依然是 200 —— 不告警就只能靠用户看到半截答案才发现。
        """
        if getattr(choice, "finish_reason", None) != "length":
            return
        usage = getattr(resp, "usage", None)
        logger.warning(
            "LLM 答案被 max_tokens 截断（finish_reason=length）：model=%s max_tokens=%s "
            "completion_tokens=%s reasoning_tokens=%s。推理模型的 reasoning token 与正文"
            "共用预算，建议调大 LLM_MAX_TOKENS。",
            self._model,
            max_tokens,
            getattr(usage, "completion_tokens", None),
            getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None),
        )

    def stream(
        self, prompt: str, *, max_tokens: int | None = None, system: str | None = None
    ) -> Iterator[str]:
        """K1 真流式：逐 delta.content 产出增量文本。

        只取 ``delta.content``：推理模型（如 qwen3.8-flash）会先产 ``reasoning_content``，
        那段思考内容不下发给用户，由前端"正在组织答案…"阶段兜住这段静默期。
        首包/末包可能出现 content 为 None 或空串的 chunk，统一跳过。
        """
        for chunk in self._client.chat.completions.create(
            **self._params(prompt, max_tokens, system, stream=True)
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
        self._assert_dim(vectors)
        return vectors

    def _assert_dim(self, vectors: list[list[float]]) -> None:
        """维度自检（K0）：模型返回维度与 EMBEDDING_DIM 不符时立刻报错。

        本类**不传** ``dimensions`` 参数（依赖模型默认维度），原先也不校验返回值，
        于是模型默认维度与配置不一致时会静默写出错误长度的向量——表现为
        "检索查得到但永远不相关"，或向量库抛底层异常，极难定位。
        """
        if not vectors:
            return
        actual = len(vectors[0])
        if actual != self._dim:
            raise ValueError(
                f"embedding 维度不一致：模型 {self._model} 返回 {actual} 维，"
                f"但配置 EMBEDDING_DIM={self._dim}。"
                f"请将 EMBEDDING_DIM 改为 {actual}（或换用支持 {self._dim} 维的模型）；"
                f"注意换维度后原向量集合不可再用，必须重建并重新导入文档。"
            )
