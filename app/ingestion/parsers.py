"""文档解析：PDF / Word / Markdown / 纯文本 → 纯文本。

依赖 pypdf 与 python-docx，均为纯 Python 包。解析失败时抛 ValueError 或异常，
由 ingestion pipeline 捕获并记到 Document.status=failed。

**为什么 Markdown 要单独走一条解析分支**：``.md`` 的语料里标题写 ``# 标题``、
表格写 ``| a | b |``、分隔行写 ``|---|---|``。这些标记没有任何语义，却会原样进入
``chunks.content``，被向量与关键词两路同时命中，最后被模型抄进答案（用户看到的
就是答案里冒出一排 ``|---|---|``）。``.docx`` 与 ``.pdf`` 都有各自的解析器把格式剥掉，
Markdown 原先和 ``.txt`` 共用纯文本分支，等于没剥。

**``.txt`` 不能走这条分支**：纯文本文件里的 ``#`` 就是字面字符，当成标题去掉是丢数据。

**清理分两段**，时机错了会出问题：

1. **解析阶段**（本模块 ``_parse_markdown``）：剥掉表格竖线与分隔行、列表符号、
   粗体/斜体/行内代码/链接、水平线、代码围栏标记。这些都不影响切分边界。
2. **切分之后**（``clean_chunks``）：只剥标题的 ``#`` 前缀。这个**必须晚于切分** ——
   切分器靠行首 ``#`` 认章节边界，提前剥掉会把相邻章节合并进同一块。

实现不引入 markdown 解析库（如 mistune / markdown-it），保持零新增依赖：这里只需要
「丢标记、留文字」，不需要渲染成 HTML 或 AST。
"""
from __future__ import annotations

import re
from pathlib import Path

SUPPORTED_EXTS = {".pdf", ".docx", ".md", ".markdown", ".txt"}

# ---- Markdown 语法正则 ----

#: 围栏代码块起止行（``` 或 ~~~，可带语言标签）。该行整行丢弃，含语言标签。
_MD_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")

#: ATX 标题。要求 ``#`` 后有空格，``#没有空格`` 不是标题。
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")

#: 行首标题前缀，供 strip_heading_marks 在**切分之后**剥掉。
_MD_HEADING_PREFIX_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)

#: 无序列表项。有序列表（``1.``）不匹配 —— 序号是流程语义，要保留。
_MD_ULIST_RE = re.compile(r"^[-*+]\s+(.*)$")

#: 行内代码
_MD_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
#: 粗体。必须先于斜体处理，否则 `**x**` 会被斜体规则吃掉外层。
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
#: 斜体
_MD_ITALIC_RE = re.compile(r"\*(.+?)\*")
#: 删除线
_MD_STRIKE_RE = re.compile(r"~~(.+?)~~")
#: 链接与图片，只保留方括号里的文字
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")


def parse_file(path: str | Path) -> str:
    """按扩展名解析文档，返回规范化纯文本。"""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".pdf":
        return _parse_pdf(p)
    if ext == ".docx":
        return _parse_docx(p)
    if ext in {".md", ".markdown"}:
        return _parse_markdown(p)
    if ext == ".txt":
        return _parse_text(p)
    raise ValueError(f"不支持的文档类型: {ext}（支持: {', '.join(sorted(SUPPORTED_EXTS))}）")


def _normalize(text: str) -> str:
    """去掉多余空行，保证段落可被 chunker 稳定切分。"""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "\n\n".join(lines)


def _parse_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    parts = [page.extract_text() or "" for page in reader.pages]
    return _normalize("\n".join(parts))


def _parse_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    return _normalize("\n".join(para.text for para in doc.paragraphs))


def _parse_text(path: Path) -> str:
    return _normalize(path.read_text(encoding="utf-8", errors="replace"))


def _md_inline(text: str) -> str:
    """剥掉行内标记，保留文字。

    顺序敏感：行内代码在最前（``**`` 出现在代码里时不该被当粗体处理），
    粗体在斜体之前（否则 ``**x**`` 被斜体规则吃掉外层星号、只剩一个 ``*``）。
    """
    text = _MD_INLINE_CODE_RE.sub(r"\1", text)
    text = _MD_BOLD_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    text = _MD_STRIKE_RE.sub(r"\1", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    return text.strip()


def _is_md_rule(line: str) -> bool:
    """水平线：``---`` / ``***`` / ``___`` / ``- - -``。"""
    core = line.replace(" ", "").replace("\t", "")
    return len(core) >= 3 and len(set(core)) == 1 and core[0] in "-*_"


def _is_md_table_separator(line: str) -> bool:
    """表格分隔行：``|---|---|`` / ``|:---:|``。去掉竖线与空白后只剩 ``-`` 与 ``:``。"""
    core = line.replace(" ", "").replace("\t", "").replace("|", "")
    return bool(core) and set(core) <= set("-:")


def _parse_markdown(path: Path) -> str:
    out: list[str] = []
    in_fence = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        # 围栏标记行（含语言标签）整行丢弃，且切换围栏状态
        if _MD_FENCE_RE.match(line):
            in_fence = not in_fence
            continue

        # 围栏内的内容是代码字面量：保留缩进，且不做行内标记处理
        if in_fence:
            out.append(line.rstrip())
            continue

        stripped = line.strip()
        if not stripped:
            continue
        if _is_md_rule(stripped) or _is_md_table_separator(stripped):
            continue

        heading = _MD_HEADING_RE.match(stripped)
        if heading:
            # 刻意保留 `#` 前缀：切分器（chunker._RE_MD_HEADING）靠它识别章节边界，
            # 这里先剥掉的话，`# 差旅报销制度` 这种没有「第X章」/「1.2」编号的标题
            # 就不再被任何规则识别，相邻两节会被切进同一个块。
            # 它会在切分之后由 clean_chunks() 剥掉，不进最终切片文本。
            out.append(f"{heading.group(1)} {_md_inline(heading.group(2))}")
            continue

        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            out.append(" ".join(c for c in cells if c))
            continue

        if stripped.startswith(">"):
            stripped = stripped.lstrip(">").strip()
            if not stripped:
                continue
            out.append(_md_inline(stripped))
            continue

        item = _MD_ULIST_RE.match(stripped)
        if item:
            stripped = item.group(1)
        out.append(_md_inline(stripped))

    return _normalize("\n".join(out))


#: 需要做「切片后清理」的扩展名 —— 只有 markdown 会把标题写成 ``# xxx``。
_MARKDOWN_EXTS = {".md", ".markdown"}


def strip_heading_marks(text: str) -> str:
    """剥掉行首的 Markdown 标题前缀。**必须在切分之后调用。**

    为什么不能提前到解析阶段：切分器靠行首 ``#`` 判断章节边界（见
    ``chunker._RE_MD_HEADING``）。解析阶段就剥掉的话，形如 ``# 差旅报销制度``
    这种既没有「第X章」也没有「1.2」编号的标题将不被任何规则识别，
    相邻章节会被合并进同一块 —— 检索粒度随之变粗。

    为什么不能对 ``.txt`` 调用：纯文本里行首的 ``# `` 是字面字符，剥掉是丢数据。
    用 .txt 装 markdown 的人，本来就没打算让程序按 markdown 解释它。
    """
    return _MD_HEADING_PREFIX_RE.sub("", text)


def clean_chunks(chunks: list[str], suffix: str) -> list[str]:
    """按来源扩展名做切片后清理，返回可直接入库的切片。

    - markdown：剥掉标题前缀（表格/粗体等在解析阶段已剥，这里只剩 ``#``）。
    - 其他格式：解析阶段已清理干净，原样返回。

    剥完只剩标题文字的块**保留**（章节名本身有信息量）；只有真正变成空串的块才丢掉 ——
    空串送去 embed 没意义，也可能被向量库拒收。
    """
    if suffix.lower() not in _MARKDOWN_EXTS:
        return chunks
    return [c for c in (strip_heading_marks(x) for x in chunks) if c.strip()]
