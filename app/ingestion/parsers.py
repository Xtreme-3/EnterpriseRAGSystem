"""文档解析：PDF / Word / Markdown / 纯文本 → 纯文本。

依赖 pypdf 与 python-docx，均为纯 Python 包。解析失败时抛 ValueError 或异常，
由 ingestion pipeline 捕获并记到 Document.status=failed。
"""
from __future__ import annotations

from pathlib import Path

SUPPORTED_EXTS = {".pdf", ".docx", ".md", ".markdown", ".txt"}


def parse_file(path: str | Path) -> str:
    """按扩展名解析文档，返回规范化纯文本。"""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".pdf":
        return _parse_pdf(p)
    if ext == ".docx":
        return _parse_docx(p)
    if ext in {".md", ".markdown", ".txt"}:
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
