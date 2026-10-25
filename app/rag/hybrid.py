"""混合检索：关键词（全文）+ 向量 双路召回融合（H1）。

无第三方依赖的分词器 + 加权归一化融合。生产走 pgvector 全文检索，
chroma 为离线/开发兜底；本模块只做与存储无关的部分。
"""
from __future__ import annotations

import re
from dataclasses import replace

from app.storage.vector_store import ScoredChunk

# 英文/数字/下划线词（保留型号、代码 token，如 gpt4 / a12）
_ASCII_WORD = re.compile(r"[a-z0-9_]+")
# 中日韩统一表意文字（中文主用块）
_CJK_CHAR = re.compile(r"[一-鿿]")


def tokenize(text: str) -> list[str]:
    """把文本切成检索词：英文/数字词整词保序 + 中文串切成重叠 2-gram。

    - 全小写；单字符 token 丢弃（"a"/"4" 无检索价值）
    - 去重保序
    - 空串 / 无可提取词返回 []
    """
    low = text.lower()
    tokens: list[str] = [w for w in _ASCII_WORD.findall(low) if len(w) >= 2]

    # 中文连续串切重叠 bigram：如「人工智能」→ 人工/工智/智能，
    # 与入库侧分词一致，保证 query 与 content 的词空间统一。
    cjk_run: list[str] = []

    def flush() -> None:
        if cjk_run:
            run = "".join(cjk_run)
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
            cjk_run.clear()

    for ch in low:
        if _CJK_CHAR.match(ch):
            cjk_run.append(ch)
        else:
            flush()
    flush()

    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


# L1 双阈值拒答门：判定「关键词路是否提供了实质证据」的覆盖度门槛。
# **不能用关键词路的 score** —— chroma 的词分是「命中词数」（整数）、pgvector 是
# ts_rank（浮点），两者量纲不通，任何字面阈值都会在换存储时静默失准。因此改用
# 「命中切片正文实际覆盖了几个查询词」这个两存储都有的无量纲口径。
# 实测（2026-10-07，双库 29 问，scripts/measure_refusal_gate.py）：
#   库外探针最多覆盖 1 个词（「美国站的退货窗口是多少天？」只命中「美国」），
#   库内问题最少覆盖 2 个 → 门槛取 2 恰好分离。
# ⚠️ 与 similarity_threshold / answer_threshold 同一纪律：换语料、加文档或换分词后
#   必须随 scripts/measure_refusal_gate.py 重跑（退出码 1 = 出现误杀）。
LEXICAL_EVIDENCE_MIN_OVERLAP = 2


def lexical_overlap(query: str, hits: list[ScoredChunk]) -> int:
    """关键词路「证据强度」：命中的切片里，最多覆盖了几个不同的查询词。

    返回 0 表示关键词路没提供实质证据（无命中，或只碰上「的/是」这类高频 2-gram）。
    见上方 ``LEXICAL_EVIDENCE_MIN_OVERLAP`` 说明，刻意不使用 ``hits`` 的 score。
    """
    terms = set(tokenize(query))
    if not terms:
        return 0
    return max(
        (sum(1 for t in terms if t in h.content.lower()) for h in hits),
        default=0,
    )


def fuse_hybrid(
    vector_hits: list[ScoredChunk],
    lexical_hits: list[ScoredChunk],
    top_k: int,
    vector_weight: float = 0.5,
) -> list[ScoredChunk]:
    """融合向量 + 关键词两路命中，按融合分降序返回前 top_k 条。

    融合分：``final = vector_weight * vec_score + (1 - vector_weight) * lex_norm``
    - ``vec_score``：向量余弦相似度（绝对分，本身 ∈ [0,1]）
    - ``lex_norm``：关键词分在本命中池内归一化（词频/ts_rank 无量纲，
      ``lex_score / max_lex_score``），使两路量纲可比
    - 一侧为空则回退另一侧原样返回；两侧都空返回 []
    - 融合分 ∈ [0,1]，前端分数条可直接展示
    """
    if not vector_hits:
        return lexical_hits[:top_k]
    if not lexical_hits:
        return vector_hits[:top_k]

    max_lex = max((h.score for h in lexical_hits), default=1.0) or 1.0
    lex_norm = {(h.document_id, h.chunk_index): h.score / max_lex for h in lexical_hits}

    key = lambda c: (c.document_id, c.chunk_index)  # noqa: E731
    merged: dict[tuple[int, int], ScoredChunk] = {}

    # 两路都命中的切片：叠加两路分数
    for h in vector_hits:
        merged[key(h)] = replace(
            h, score=vector_weight * h.score + (1 - vector_weight) * lex_norm.get(key(h), 0.0)
        )
    # 仅关键词命中的切片：补上它的关键词贡献（召回增强的核心）
    for h in lexical_hits:
        if key(h) not in merged:
            merged[key(h)] = replace(h, score=(1 - vector_weight) * h.score / max_lex)

    return sorted(merged.values(), key=lambda c: c.score, reverse=True)[:top_k]
