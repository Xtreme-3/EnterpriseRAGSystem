"""生成器：拼接上下文与引用编号 → 调 LLM 生成带引用的答案。"""
from __future__ import annotations

from app.providers.base import LLMProvider
from app.storage.vector_store import ScoredChunk

PROMPT_TEMPLATE = """你是企业内部知识库问答助手。请仅依据下面提供的资料回答用户的问题。

【资料】
{context}

【用户问题】
{query}

要求：
1. 用简体中文回答，简明准确；
2. 回答中引用某条资料时，在对应句子末尾标注来源编号，如 [1]、[2]；
3. 若资料中没有相关信息，请明确说明「资料库中未找到相关信息」，不要编造。
"""


class Generator:
    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    def build_prompt(self, query: str, chunks: list[ScoredChunk]) -> str:
        context = "\n\n".join(f"[{i + 1}] {c.content}" for i, c in enumerate(chunks))
        return PROMPT_TEMPLATE.format(context=context, query=query)

    def generate(self, query: str, chunks: list[ScoredChunk]) -> str:
        if not chunks:
            return "资料库中未找到相关信息。"
        return self._llm.complete(self.build_prompt(query, chunks))
