"""模型供应商抽象接口：LLM / Embedding / Rerank 三个可插拔插槽。

具体实现通过 app.providers.factory 按配置装配，业务代码只依赖本模块接口。
"""
from __future__ import annotations

from abc import ABC, abstractmethod


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


class RerankProvider(ABC):
    """查询-候选 重排序，可选插槽（I1 做实）。"""

    @abstractmethod
    def rerank(self, query: str, texts: list[str], scores: list[float]) -> list[float]:
        """返回每条候选文本的重排相关性分数（越大越相关），顺序与 texts/scores 一致。

        ``scores`` 为原始检索分（与 texts 对齐），Noop/混合实现可据此兜底。
        """
