"""Mock 供应商：无 API key 时也能跑通全链路。

Embedding: 哈希词袋 → 确定性伪向量，相似文本得到相似向量。
LLM: 直接展示检索到的资料原文，标注来源索引。
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
    """无大模型时的伪 LLM：从检索块中提取事实性句子（含数字/规格/标准代号），
    聚焦呈现，而不是整块摊开。检索已用向量匹配选出相关片段，Mock 模式如实呈现其中的事实。

    注意：Mock 模式没有语义理解，做不了"总结/提炼/组织语言"。
    要得到连贯的 AI 答案，必须配置真实大模型（RAG_PROVIDER=dashscope 或 zhipu + API Key）。
    """

    # 事实句判定：含数字、比较符、单位、标准代号等"具体信息"
    _FACT_RE = re.compile(r"[\d≤≥<>×÷％%]|ISO\s*\d+|GB\s*\d|REACH|GPSR|EC\s*[\d/]")
    # 章节标题形态："2 适用范围"、"4.2 五金件标准"、"1 基本资质门槛"（无具体事实，剔除）
    _TITLE_RE = re.compile(r"^\d+(?:\.\d+)*\s*[一-鿿、，,。\- ]{1,16}$")
    # 去掉列表前缀（"- "、"1. "、"2.1 "等），保留句子正文
    _LIST_PREFIX = re.compile(r"^(?:[-*]+|\d+(?:\.\d+)*[.、])\s*")

    def complete(self, prompt: str, *, max_tokens: int = 1024) -> str:
        query, context_blocks = self._parse_prompt(prompt)
        if not context_blocks:
            return "资料库中未找到相关信息。"

        facts = self._collect_facts(context_blocks)
        if facts:
            lines = [
                f"根据资料库内容，与「{query}」相关的事实（Mock 模式，未经大模型总结）：\n"
            ]
            for i, (src, sent) in enumerate(facts, 1):
                lines.append(f"{i}. [来源{src}] {sent}")
            lines.append(
                "\n" + "─" * 50 + "\n"
                "提示: 以上为资料原文中的事实条目，未经大模型语言组织。\n"
                "   配置真实大模型后会自动生成连贯答案。\n"
                "   方法: 在 .env 中设置 RAG_PROVIDER=dashscope 或 zhipu，填写 API Key。"
            )
            return "\n".join(lines)

        # 回退：检索块内无可提取的事实，直接展示原文开头
        lines = [f"以下是资料库中与「{query}」相关的资料片段（Mock 模式）：\n"]
        for i, block in enumerate(context_blocks, 1):
            display = block[:300] + ("…" if len(block) > 300 else "")
            lines.append(f"[来源{i}] {display}")
        return "\n".join(lines)

    @classmethod
    def _collect_facts(cls, blocks: list[str]) -> list[tuple[int, str]]:
        """提取各检索块中的事实性句子，按块序去重返回，最多 8 条。"""
        seen: set[str] = set()
        facts: list[tuple[int, str]] = []
        for src, block in enumerate(blocks, 1):
            for sent in cls._fact_sentences(block):
                key = sent[:16]
                if key in seen:
                    continue
                seen.add(key)
                facts.append((src, sent))
                if len(facts) >= 8:
                    return facts
        return facts

    @staticmethod
    def _fact_sentences(block: str) -> list[str]:
        """切句并挑选含数字/规格/标准代号的事实句（剔除纯章节标题）。"""
        out: list[str] = []
        for part in re.split(r"[。！？；;\n]", block):
            s = MockLLM._LIST_PREFIX.sub("", part.strip())
            s = s.replace("**", "").strip()
            if not (6 <= len(s) <= 120 and MockLLM._FACT_RE.search(s)):
                continue
            if MockLLM._TITLE_RE.match(s):
                continue
            out.append(s)
        return out

    @staticmethod
    def _parse_prompt(prompt: str) -> tuple[str, list[str]]:
        """从 Generator 的 prompt 模板中提取 query 和上下文块。"""
        # 提取【用户问题】
        query_match = re.search(r"【用户问题】\s*\n(.+)", prompt)
        query = query_match.group(1).strip() if query_match else ""

        # 只截取【资料】…【用户问题】之间的内容解析 [N] 块：
        # J1 在【资料】前新增了【对话历史】块，若历史文本含 "[数字]" 不会污染块解析。
        region = prompt
        m = re.search(r"【资料】(.*?)【用户问题】", prompt, re.DOTALL)
        if m:
            region = m.group(1)

        blocks: list[str] = []
        for fm in re.finditer(r"\[\d+\]\s*(.+?)(?=\[\d+\]|$)", region, re.DOTALL):
            blocks.append(fm.group(1).strip())
        return query, blocks
