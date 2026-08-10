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
