"""生成器：拼接上下文与引用编号 → 调 LLM 生成带引用的答案。

J1 多轮对话：可选注入【对话历史】块（在【资料】之前），帮助模型消解指代。
历史块只在有 history 时出现，无历史时模板与单轮完全一致（回归安全）。

K3 答案质量，两处结构改动：

1. **角色分离**：系统规则从 user 段挪到独立的 ``system`` 槽位。真模型对 system 的
   遵循度显著高于把同样的规则塞在长 user prompt 里，user 段只留「资料 + 问题」。
   模板见下面的 ``SYSTEM_PROMPT`` 与 ``USER_TEMPLATE``。
2. **来源可溯**：每块资料渲染来源头「[N]（来源：文件名 · 第 M 块）」，让模型能写出
   「根据《供应商管理制度》第 4.2 条…」，而不是笼统的"资料显示"。
   ``filenames`` 由调用方（RagPipeline）在生成**之前**从元数据库解析后传入；
   拿不到文件名时退回旧的「[N] 正文」格式——编号不丢，只是少一层元信息。
"""
from __future__ import annotations

import re
from collections.abc import Iterator

from app.providers.base import LLMProvider
from app.storage.vector_store import ScoredChunk

# 检索为空时的固定答复（非流式 generate 与流式 stream 共用同一文案，避免两处漂移）
NO_HIT_ANSWER = "资料库中未找到相关信息。"

# 检索有命中、但模型一个字都没返回时的提示（K3）：不把空串交给前端变成"空气泡"。
# 与 NO_HIT_ANSWER 分开：两者语义不同，前者是"资料里没有"，后者是"模型没答上"。
LLM_EMPTY_ANSWER = "模型未返回内容，请重试；若反复出现，请检查 LLM_MAX_TOKENS 是否被推理 token 占满。"

# 系统规则（K3）：走 LLM 的 system 槽位，不再拼进 user prompt。
SYSTEM_PROMPT = """你是企业内部知识库问答助手，依据给定资料回答员工的提问。

要求：
1. 只依据【资料】中的内容作答，不要使用资料之外的任何知识；
2. 用简体中文回答，简明准确，先给结论再给依据；
3. 资料中的金额、比例、数量、条款号必须原样引用，不得改写、换算或四舍五入；
4. 引用资料时，在句末标注它的来源编号（如 [1]），并写明来源文档名与章节号，
   例如「根据《供应商管理制度》4.2 节」「《跨境售后政策》第三章」；
   资料若未标明章节号，就只写文档名；不要只给编号而不说来自哪份文档；
5. 若【资料】中没有足够信息，直接回答「资料库中未找到相关信息」，不要猜测或编造。"""

# user 段只留资料与问题；{history} 无历史时渲染成空串（单轮模板与 J1 之前一致）
USER_TEMPLATE = """{history}【资料】
{context}

【用户问题】
{query}"""

# 来源头。块号对人类是 1-based（底层 chunk_index 从 0 起，这里 +1 展示）
_SOURCE_HEADER = "[{idx}]（来源：{filename} · 第 {chunk} 块）"

# 引用编号：[N]。编号契约由上面的来源头定义 —— 第 N 块资料在 prompt 里就是 [N]，1-based。
# 负向前瞻 `(?!\()` 排除 markdown 链接 `[1](url)`：那是超链接，不是来源引用。
_CITATION_RE = re.compile(r"\[(\d+)\](?!\()")


def citation_issues(answer: str, source_count: int) -> list[int]:
    """答案里引用了不存在的来源编号 → 返回越界编号（去重、升序）；无引用返回 []。

    模型可能写出本次检索根本没有的编号（如只命中 5 块却写 ``[9]``）——
    那种引用在前端点不开，而服务端原本完全无痕。把它变成可观测信号（K3 批 2）。
    """
    cited = {int(m) for m in _CITATION_RE.findall(answer)}
    return sorted(n for n in cited if n < 1 or n > source_count)


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

    @staticmethod
    def _render_block(index: int, chunk: ScoredChunk, filenames: dict[int, str] | None) -> str:
        """渲染单块资料：有文件名则带来源头，否则退回「[N] 正文」。"""
        filename = (filenames or {}).get(chunk.document_id)
        if not filename:
            return f"[{index}] {chunk.content}"
        head = _SOURCE_HEADER.format(idx=index, filename=filename, chunk=chunk.chunk_index + 1)
        return f"{head}\n{chunk.content}"

    def build_prompt(
        self,
        query: str,
        chunks: list[ScoredChunk],
        history: list | None = None,
        filenames: dict[int, str] | None = None,
    ) -> str:
        """拼 user 段（system 段见 ``SYSTEM_PROMPT``，由 generate/stream 单独下发）。"""
        context = "\n\n".join(
            self._render_block(i + 1, c, filenames) for i, c in enumerate(chunks)
        )
        return USER_TEMPLATE.format(
            history=self._history_block(history),
            context=context,
            query=query,
        )

    def generate(
        self,
        query: str,
        chunks: list[ScoredChunk],
        history: list | None = None,
        filenames: dict[int, str] | None = None,
    ) -> str:
        if not chunks:
            return NO_HIT_ANSWER
        prompt = self.build_prompt(query, chunks, history=history, filenames=filenames)
        answer = self._llm.complete(prompt, system=SYSTEM_PROMPT)
        # 推理模型把预算耗在 reasoning 上时会返回空正文（见 LLM_EMPTY_ANSWER 注释）
        return answer if answer.strip() else LLM_EMPTY_ANSWER

    def stream(
        self,
        query: str,
        chunks: list[ScoredChunk],
        history: list | None = None,
        filenames: dict[int, str] | None = None,
    ) -> Iterator[str]:
        """K1 真流式：逐段产出答案增量。

        检索为空时**不调用大模型**，直接产出固定文案（与 generate 同文案）。
        与 ``generate`` 共用同一个 system 与同一份 prompt（避免两处漂移导致
        流式与非流式答案风格不一致）。
        """
        if not chunks:
            yield NO_HIT_ANSWER
            return
        prompt = self.build_prompt(query, chunks, history=history, filenames=filenames)
        produced = False
        for token in self._llm.stream(prompt, system=SYSTEM_PROMPT):
            produced = produced or bool(token)
            yield token
        # 一个非空 token 都没有：给可读提示，别让前端一直空转/显示气泡。
        # 注意只在**正常收尾**后兜底——中途抛异常照原样冒泡（半截答案比静默失败好排查）。
        if not produced:
            yield LLM_EMPTY_ANSWER
