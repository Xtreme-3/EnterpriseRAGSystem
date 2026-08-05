"""Mock 供应商：无 API key 时也能跑通全链路。

Embedding 用「哈希词袋」生成确定性伪向量——相似文本得到相似向量，因此
检索排序在语义上是「近似的、可验证的」；LLM 返回固定模板答案。
"""
from __future__ import annotations

import hashlib
import math
import re

from app.providers.base import EmbeddingProvider, LLMProvider

_CJK = "一-鿿"


class MockEmbedding(EmbeddingProvider):
    """确定性伪向量：词/汉字哈希到固定桶，TF 归一化。"""

    DIM = 64

    @property
    def dim(self) -> int:
        return self.DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.DIM
        for feature in self._features(text):
            idx = int(hashlib.md5(feature.encode("utf-8")).hexdigest(), 16) % self.DIM
            vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    @staticmethod
    def _features(text: str) -> list[str]:
        """拉丁词整词 + 中文字符逐个，作为特征。"""
        feats: list[str] = []
        for token in re.findall(rf"[a-z0-9]+", text.lower()):
            feats.append(token)
        for ch in text.lower():
            if "一" <= ch <= "鿿":
                feats.append(ch)
        return feats


class MockLLM(LLMProvider):
    def complete(self, prompt: str, *, max_tokens: int = 1024) -> str:
        return (
            "【Mock 回复】已基于资料库内容完成答复。\n"
            "如需真实大模型答案，请复制 .env.example 为 .env，"
            "填写 RAG_PROVIDER=dashscope（或 zhipu）及对应 API Key 后重跑。"
        )
