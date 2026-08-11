"""查询改写（J1 多轮对话）：把带指代/省略的追问改写成独立可检索的问句。

双策略：
- 无历史 → 原样返回（单轮零开销，行为与改动前完全一致）
- **规则策略**（默认，mock/离线）：当前问句"短 / 以指代连词开头"且历史非空时，
  检索词 = 最近一轮用户问句 + " · " + 当前问句，保留上文实体供检索召回；
  自包含长问句原样返回。确定性、无需 LLM、可离线测试。
- **LLM 策略**（配真实大模型，`use_llm=True`）：改写 prompt 让模型输出不依赖
  上文的独立搜索词；输出为空 / 异常 / 模型回退文案时自动降级到规则策略。

结构同 I1 Rerank 的 Noop/Mock/Real 三实现：默认规则（=离线 Mock），配真模型自动升级。
"""
from __future__ import annotations

import re

from app.providers.base import LLMProvider

# 短问句判定上限（字符）：超过视为自包含，不做拼接
_SHORT_LIMIT = 20
# 指代/省略连词：命中说明当前问句大概率依赖上文（"那/这/它/再/具体/为什么…"）
_FOLLOWUP_MARK = re.compile(
    r"^(那|那么|那它|它|这|这个|那个|这些|那些|其中|再|又|然后|之后|接下来|"
    r"另外|除此之外|具体|详细|更多|为什么|怎么|怎么样|咋|如何|多少|哪些|其他|别的|是否|呢)"
)
# LLM 输出若含这些文案（Mock 兜底/未找到）视为改写失败，回退规则策略
_FALLBACK_MARKERS = ("资料库中未找到相关信息", "Mock 模式")

_LLM_PROMPT = """你是检索查询改写助手。用户在多轮对话中问出"当前问句"，它可能含指代或省略（如"那超过一万呢？"）。
请把当前问句改写成一句**不依赖上文、独立可检索**的问句：合并指代所指的实体、补全省略的限定词，保留核心语义。
只输出改写后的一句问句，不要任何解释、不要加引号。

【对话历史】
{history}

【当前问句】
{query}

改写后问句："""


class QueryRewriter:
    """把多轮追问改写成独立检索问句。``history`` 为含 ``role``/``content`` 的轮次列表。"""

    def __init__(self, llm: LLMProvider | None = None, *, use_llm: bool = False) -> None:
        self._llm = llm
        self._use_llm = bool(use_llm and llm is not None)

    def rewrite(self, query: str, history: list | None = None) -> str:
        """返回检索用问句。无历史 → 原样（单轮零开销）。"""
        turns = history or []
        if not turns:
            return query
        if self._use_llm:
            rewritten = self._llm_rewrite(query, turns)
            # LLM 改写失败（空/异常/兜底文案）时降级规则策略
            if rewritten and not any(m in rewritten for m in _FALLBACK_MARKERS):
                return rewritten
        return self._rule_rewrite(query, turns)

    # ---- 规则策略（离线确定性） ----

    def _rule_rewrite(self, query: str, turns: list) -> str:
        last_user = self._last_user_query(turns)
        if not last_user:
            return query
        if self._is_followup(query):
            return f"{last_user} · {query}"
        return query

    @staticmethod
    def _is_followup(query: str) -> bool:
        q = query.strip()
        return len(q) <= _SHORT_LIMIT or bool(_FOLLOWUP_MARK.match(q))

    @staticmethod
    def _last_user_query(turns: list) -> str | None:
        for t in reversed(turns):
            if getattr(t, "role", "") == "user":
                return getattr(t, "content", "")
        return None

    # ---- LLM 策略（真实大模型） ----

    def _llm_rewrite(self, query: str, turns: list) -> str:
        hist_lines = "\n".join(
            f"{'用户' if getattr(t, 'role', '') == 'user' else '助手'}：{getattr(t, 'content', '')}"
            for t in turns
        )
        prompt = _LLM_PROMPT.format(history=hist_lines, query=query)
        try:
            return self._llm.complete(prompt, max_tokens=128).strip()
        except Exception:
            return ""


def build_rewriter(settings, llm: LLMProvider | None = None) -> QueryRewriter:
    """按配置构建改写器：mock/离线 → 规则策略；真实模型（dashscope/zhipu）→ LLM 策略。"""
    return QueryRewriter(llm, use_llm=settings.rag_provider != "mock")
