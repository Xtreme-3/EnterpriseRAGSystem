"""递归字符切片：按 段落→句子→空白→字符 的优先级逐级切分。

自研实现（约 60 行），替代 langchain-text-splitters，避免引入 langchain 依赖。
每块目标长度 chunk_size 字符，相邻块共享 chunk_overlap 字符以保证语义连续性。
"""
from __future__ import annotations

import re

# 分隔符按优先级排列：双换行(段落) → 单换行 → 中文/英文句号 → 空白 → 字符
_SEPARATORS = ["\n\n", "\n", "。", "！", "？", "!?", ". ", " ", ""]

_RE_MULTI_NEWLINES = re.compile(r"\n{2,}")
# 用于句子级分割的中文/英文句子结束符（仅作 fallback 统计用）
_SENT_END = re.compile(r"[。！？!?]")


def split_text(text: str, chunk_size: int = 800, chunk_overlap: int = 120) -> list[str]:
    """把长文本切成若干块，每块目标 <= chunk_size。"""
    text = _collapse(text)
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]
    chunks = _split(text, _SEPARATORS, chunk_size, chunk_overlap)
    return _apply_overlap(chunks, chunk_size, chunk_overlap)


def _collapse(text: str) -> str:
    """压缩多余空行与行尾空白，便于稳定切分。"""
    text = text.strip()
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return _RE_MULTI_NEWLINES.sub("\n\n", text)


def _split(text: str, seps: list[str], chunk_size: int, overlap: int) -> list[str]:
    if len(text) <= chunk_size:
        return [text] if text.strip() else []
    if not seps:
        return _hard_split(text, chunk_size, overlap)

    sep = seps[0]
    parts = _split_on(text, sep)
    if len(parts) <= 1:
        return _split(text, seps[1:], chunk_size, overlap)

    chunks: list[str] = []
    buf = ""
    for part in parts:
        if len(part) > chunk_size:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.extend(_split(part, seps[1:], chunk_size, overlap))
        elif buf and len(buf) + len(sep) + len(part) > chunk_size:
            chunks.append(buf)
            buf = part
        else:
            buf = buf + sep + part if buf else part
    if buf.strip():
        chunks.append(buf)
    return chunks


def _split_on(text: str, sep: str) -> list[str]:
    if sep == "":
        return list(text)
    parts = text.split(sep)
    # 空格切分时保留空串以维持原间距大致形状；其他分隔符丢弃空段
    if sep == " ":
        return parts
    return [p for p in parts if p]


def _hard_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    """无自然断点时的硬切：步长 = chunk_size - overlap。"""
    step = chunk_size - overlap
    if step <= 0:
        step = chunk_size
    return [
        text[i : i + chunk_size]
        for i in range(0, len(text), step)
        if text[i : i + chunk_size].strip()
    ]


def _apply_overlap(chunks: list[str], chunk_size: int, overlap: int) -> list[str]:
    """相邻块共享 overlap 字符：后一块头部补上前一块的尾部。"""
    if overlap <= 0 or len(chunks) <= 1:
        return chunks
    out: list[str] = []
    for i, chunk in enumerate(chunks):
        if i == 0:
            out.append(chunk)
            continue
        prev_tail = chunks[i - 1][-overlap:]
        merged = prev_tail + chunk
        if i == len(chunks) - 1:
            # 最后一块不截断，避免尾部内容丢失
            out.append(merged)
        else:
            out.append(merged[:chunk_size] if len(merged) > chunk_size else merged)
    return out


def _count_sentences(text: str) -> int:
    return len(_SENT_END.findall(text))
