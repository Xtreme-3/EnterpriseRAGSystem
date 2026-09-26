"""生成器：拼接上下文与引用编号 → 调 LLM 生成带引用的答案。

J1 多轮对话：可选注入【对话历史】块（在【资料】之前），帮助模型消解指代。
历史块只在有 history 时出现，无历史时模板与单轮完全一致（回归安全）。
"""
from __future__ import annotations

from collections.abc import Iterator

from app.providers.base import LLMProvider
from app.storage.vector_store import ScoredChunk

# 检索为空时的固定答复（非流式 generate 与流式 stream 共用同一文案，避免两处漂移）
NO_HIT_ANSWER = "资料库中未找到相关信息。"

PROMPT_TEMPLATE = """你是企业内部知识库问答助手。请仅依据下面提供的资料回答用户的问题。

{history}【资料】
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

    @staticmethod
    def _history_block(history: list | None) -> str:
        """把历史轮次渲染成【对话历史】块；无历史返回空串（不破坏单轮模板）。"""
        if not history:
            return ""
        lines = [f"{'用户' if getattr(t, 'role', '') == 'user' else '助手'}：{getattr(t, 'content', '')}"
                 for t in history]
        return "【对话历史】\n" + "\n".join(lines) + "\n\n"

    def build_prompt(self, query: str, chunks: list[ScoredChunk], history: list | None = None) -> str:
        context = "\n\n".join(f"[{i + 1}] {c.content}" for i, c in enumerate(chunks))
        return PROMPT_TEMPLATE.format(
            history=self._history_block(history),
            context=context,
            query=query,
        )

    def generate(self, query: str, chunks: list[ScoredChunk], history: list | None = None) -> str:
        if not chunks:
            return NO_HIT_ANSWER
        return self._llm.complete(self.build_prompt(query, chunks, history=history))

    def stream(
        self, query: str, chunks: list[ScoredChunk], history: list | None = None
    ) -> Iterator[str]:
        """K1 真流式：逐段产出答案增量。

        检索为空时**不调用大模型**，直接产出固定文案（与 generate 同文案）。
        """
        if not chunks:
            yield NO_HIT_ANSWER
            return
        yield from self._llm.stream(self.build_prompt(query, chunks, history=history))
