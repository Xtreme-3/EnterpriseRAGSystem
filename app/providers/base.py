"""模型供应商抽象接口：LLM / Embedding / Rerank 三个可插拔插槽。

具体实现通过 app.providers.factory 按配置装配，业务代码只依赖本模块接口。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator


class EmbeddingProvider(ABC):
    """文本向量化。"""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """返回与 texts 等长的向量列表。"""

    @property
    @abstractmethod
    def dim(self) -> int:
        """向量维度，用于初始化向量库集合。"""


class LLMProvider(ABC):
    """大模型文本生成。"""

    @abstractmethod
    def complete(self, prompt: str, *, max_tokens: int = 1024) -> str:
        """单轮补全，返回完整文本。"""

    def stream(self, prompt: str, *, max_tokens: int = 1024) -> Iterator[str]:
        """逐段产出增量文本（K1 真流式）。

        默认实现等于「完整生成后一次性 yield」，因此**未覆写本方法的实现
        （如 MockLLM）行为与改动前完全一致**——离线与已有测试零回归。
        支持流式的实现应覆写本方法（见 OpenAICompatLLM.stream）。
        """
        yield self.complete(prompt, max_tokens=max_tokens)


class RerankProvider(ABC):
    """查询-候选 重排序，可选插槽（I1 做实）。"""

    @abstractmethod
    def rerank(self, query: str, texts: list[str], scores: list[float]) -> list[float]:
        """返回每条候选文本的重排相关性分数（越大越相关），顺序与 texts/scores 一致。

        ``scores`` 为原始检索分（与 texts 对齐），Noop/混合实现可据此兜底。
        """
