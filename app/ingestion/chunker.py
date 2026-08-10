"""递归字符切片：按 段落→句子→空白→字符 的优先级逐级切分。

自研实现（约 60 行），替代 langchain-text-splitters，避免引入 langchain 依赖。
每块目标长度 chunk_size 字符，相邻块共享 chunk_overlap 字符以保证语义连续性。

H2：另提供 split_structure（结构优先语义切分）与 split_semantic（+句级 embedding 微调），
详见 docs/requirements/H2-semantic-chunking.md。
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable

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


# ---- 语义切分（H2）----

# 标题行判定（启发式，只认可靠形态，降低误判）
_RE_MD_HEADING = re.compile(r"^#{1,6}\s+\S")
# 章节条款：第X章/节/条，且紧跟的不是句末标点（排除"第1段。"这类正文句）
_RE_CN_HEADING = re.compile(r"^第\s*[0-9一二三四五六七八九十百千]+\s*[章节条款]\s*(?![。，、；：！？])\S")
# 编号条款：1 适用范围 / 4.2 五金件标准
_RE_NUM_HEADING = re.compile(r"^\d+(?:\.\d+)*\s*[一-鿿]")


def split_structure(
    text: str,
    chunk_size: int = 800,
    chunk_overlap: int = 120,
) -> list[str]:
    """结构优先语义切分（H2）：标题/编号条款为新块起点，段落并入直至下一个标题。

    无结构信号的文档退化为递归定长切分；超长节按段落/句子边界再切。
    注意：语义切分在语义边界切块，不做跨块字符重叠（chunk_overlap 仅对 fixed 生效），
    避免上一块的尾字污染下一块的主题。
    """
    text = _collapse(text)
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    blocks = _split_by_headings(text)
    resized: list[str] = []
    for b in blocks:
        if len(b) <= chunk_size:
            resized.append(b)
        else:
            resized.extend(_split(b, _SEPARATORS, chunk_size, 0))
    return _merge_short_chunks(resized, chunk_size)


def split_semantic(
    text: str,
    embed: Callable[[list[str]], list[list[float]]],
    threshold: float = 0.65,
    chunk_size: int = 800,
    chunk_overlap: int = 120,
) -> list[str]:
    """结构优先 + 句级 embedding 语义微调（H2）。

    先用标题分节（不按长度裁剪），对超过 chunk_size 的节，按相邻句子的余弦相似度
    找主题断层（低于 threshold 切）；仍超长的段按句子边界打包。embed 可注入
    （离线单测传假函数，生产传 EmbeddingProvider.embed）。
    """
    text = _collapse(text)
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    refined: list[str] = []
    for block in _split_by_headings(text):
        if len(block) <= chunk_size:
            refined.append(block)
            continue
        refined.extend(_split_by_topic(block, embed, threshold, chunk_size))
    # 不做跨块重叠（同 split_structure），保持块的主题纯度
    return refined


def _is_heading_line(line: str) -> bool:
    """启发式判定一行是否为标题（结构信号）。

    只认可靠形态（Markdown # / 第X章 / 编号条款），避免把普通短句误判为标题。
    """
    line = line.strip()
    if not line:
        return False
    return bool(
        _RE_MD_HEADING.match(line)
        or _RE_CN_HEADING.match(line)
        or _RE_NUM_HEADING.match(line)
    )


def _split_by_headings(text: str) -> list[str]:
    """按标题切成段落组：标题 + 其后内容为一个语义块，直到下一个标题。"""
    paragraphs = text.split("\n\n")
    out: list[str] = []
    buf = ""
    for para in paragraphs:
        if _is_heading_line(para):
            if buf:
                out.append(buf.strip())
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf.strip():
        out.append(buf.strip())
    return [b for b in out if b.strip()]


def _merge_short_chunks(chunks: list[str], chunk_size: int) -> list[str]:
    """把过短的块（近似空块，如只有标题）并入前一块；合并后不超过 chunk_size 上限。

    min_len 取 min(48, chunk_size//4)：只合并"标题行/一两句"级别的碎块，
    不吞掉真实小节（一个 300 字的小节在 800 默认下应保持独立）。
    """
    if len(chunks) <= 1:
        return chunks
    min_len = min(48, chunk_size // 4)
    out: list[str] = []
    for c in chunks:
        if out and len(out[-1]) < min_len and len(out[-1]) + len(c) <= chunk_size:
            out[-1] = f"{out[-1]}\n\n{c}"
        else:
            out.append(c)
    return out


def _split_by_topic(
    block: str,
    embed: Callable[[list[str]], list[list[float]]],
    threshold: float,
    chunk_size: int,
) -> list[str]:
    """把超长块按句级相似度断层切成若干语义段；仍超长的段按句子边界硬拆。"""
    sents = _split_sentences(block)
    if len(sents) <= 1:
        return _split(block, _SEPARATORS, chunk_size, 0)

    vecs = embed(sents)
    breaks = {i + 1 for i in range(len(vecs) - 1) if _cosine(vecs[i], vecs[i + 1]) < threshold}

    pieces: list[str] = []
    cur: list[str] = []
    for i, sent in enumerate(sents):
        cur.append(sent)
        if i + 1 in breaks:
            pieces.append(_join_sents(cur))
            cur = []
    if cur:
        pieces.append(_join_sents(cur))

    final: list[str] = []
    for piece in pieces:
        if len(piece) <= chunk_size:
            final.append(piece)
        else:
            final.extend(_split_piece_by_sentences(piece, chunk_size))
    return [c for c in final if c.strip()]


def _split_sentences(text: str) -> list[str]:
    """按中文句末标点/换行切成句子（保留句末标点）。"""
    parts = re.split(r"(?<=[。！？；])\s*|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def _join_sents(sents: list[str]) -> str:
    return "".join(sents)


def _split_piece_by_sentences(piece: str, chunk_size: int) -> list[str]:
    """把超长语义段按句子贪心打包到 chunk_size；单句超长则递归硬切。"""
    out: list[str] = []
    buf = ""
    for sent in _split_sentences(piece):
        if len(sent) > chunk_size:
            if buf:
                out.append(buf)
                buf = ""
            out.extend(_split(sent, _SEPARATORS, chunk_size, 0))
        elif buf and len(buf) + len(sent) > chunk_size:
            out.append(buf)
            buf = sent
        else:
            buf += sent
    if buf:
        out.append(buf)
    return [c for c in out if c.strip()]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
