"""把 ``docs/sample-docs/*.md`` 转成 PDF 与 DOCX。

这些产物是**知识库的原始语料**：``docs/sample-docs/*.pdf`` 会被上传进 ``kb_1``，
PDF 抽取出的文本就是检索命中的内容。所以这个脚本的输出质量直接等于语料质量。

**历史缺陷（2026-09-27 修复）**

原实现把 md 逐行分派到四个分支，只有**普通段落**分支做了
``re.sub(r"\\*\\*(.+?)\\*\\*", ...)``；**表格行**与**引用行**在拼接完就 ``continue``，
绕过了那一步，而**单星号斜体**从头到尾没人处理。后果是星号落进 PDF、再随 PDF 文本
进了向量库 —— 实测 ``全球优选_跨境售后政策.pdf`` 2622 字里带 **30 个字面星号**，
检索切片长这样::

    美国  |  因州而异（多数无强制）  |  **30 天无理由退货**  |  FTC Mail Order Rule

同时模板里 ``✅`` / ``⚠️`` 这类 emoji 也是原样写进 PDF 的（中文字体没有对应字形，
渲染出来是空白或方框）。

**现在的结构**

``clean_inline`` → ``parse_md_blocks`` → 两个渲染器。清理只发生在 ``parse_md_blocks``
里一处，渲染器只消费 block，**任何分支都不可能再绕过清理**。表格被识别为独立 block，
PDF 里画成真表格（而不是把单元格用 ``|`` 串成一行）。

用法::

    python scripts/generate_sample_docs.py            # 转换 docs/sample-docs 下全部 md
    python scripts/generate_sample_docs.py --only 产品手册   # 只转文件名含该子串的

依赖 ``fpdf2`` 与 ``python-docx``；缺失时 ``fpdf2`` 会自动 pip 安装
（该安装刻意放在函数内，保证**导入本模块无副作用** —— pytest 收集测试
时不希望触发网络安装）。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent  # scripts/ 的上一级即项目根
SRC = ROOT / "docs" / "sample-docs"
OUT = ROOT / "docs" / "sample-docs"


# ============================================================
# 行内清理
# ============================================================

# emoji 与图形符号。刻意**只**圈这几个区段，不含 U+2190–U+21FF（箭头 →）、
# U+2200–U+22FF（数学符号 ≥ ≤ Δ）、U+2010–U+205F（破折号 — 与间隔号 ·）——
# 那些是正文语义，删掉会直接改坏文档。
_EMOJI_CLASS = (
    "\U0001f000-\U0001faff"  # 各种图形、补充符号（🔨 🎯 📌…）
    "\U00002600-\U000027bf"  # 杂项符号 + 装饰符号（✅ ⚠ ❌…）
    "\U00002b00-\U00002bff"  # 杂项符号与箭头（⬜…）
    "\ufe0f"                 # 变体选择符（⚠️ 的后半个码位）
    "\u200d"                 # 零宽连接符
)
_EMOJI_RE = re.compile(f"[{_EMOJI_CLASS}]+")

# 顺序不能反：**加粗** 必须先处理，否则单星号规则会先把 `**` 吃掉一半，
# 留下一个孤立的 `*`（`**x**` → `*x*` → `x*`）。
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"\*(.+?)\*")
_MULTISPACE_RE = re.compile(r"[ \t]{2,}")


def clean_inline(text: str) -> str:
    """去掉行内 markdown 标记与 emoji，返回可直接写进 PDF / DOCX 的纯文本。

    表格行与引用行**必须**也走这里 —— 原缺陷正是因为它们各有一条绕过清理的
    早退路径。清理后多余的空白一并收掉，避免留下 ``96.2% `` 这种尾随空格。
    """
    text = _BOLD_RE.sub(r"\1", text)
    text = _ITALIC_RE.sub(r"\1", text)
    text = _EMOJI_RE.sub("", text)
    text = _MULTISPACE_RE.sub(" ", text)
    return text.strip()


# ============================================================
# md → 结构化 block
# ============================================================

# ("heading", (level, text)) / ("para", text) / ("quote", text)
# ("rule", None) / ("table", list[list[str]]) / ("code", text)
Block = tuple[str, Any]

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_SEP_CELL_RE = re.compile(r"^[-:]+$")


def _split_table_row(line: str) -> list[str]:
    return [clean_inline(c) for c in line.strip().strip("|").split("|")]


def parse_md_blocks(text: str) -> list[Block]:
    """把 md 文本解析成 block 列表。**唯一的清理入口**（所有分支都过 clean_inline）。

    表格按"连续 ``|`` 行"聚合，分隔行（``|---|---|``）丢弃；代码围栏 ````` ``` ````
    的标记行丢弃、内容聚成一个 ``code`` block（否则反引号会原样进 PDF）。
    """
    blocks: list[Block] = []
    table_rows: list[list[str]] = []
    code_lines: list[str] = []
    in_code = False

    def flush_table() -> None:
        if table_rows:
            blocks.append(("table", list(table_rows)))
            table_rows.clear()

    def flush_code() -> None:
        if code_lines:
            blocks.append(("code", "\n".join(code_lines)))
            code_lines.clear()

    for raw in text.splitlines():
        line = raw.strip()

        # 代码围栏：标记行本身不输出，只切换状态
        if line.startswith("```"):
            if in_code:
                flush_code()
                in_code = False
            else:
                flush_table()
                in_code = True
            continue

        if in_code:
            code_lines.append(line)
            continue

        if not line:
            flush_table()
            continue

        if line.startswith("|") and line.endswith("|"):
            cells = _split_table_row(line)
            if cells and all(_SEP_CELL_RE.match(c) for c in cells):
                continue  # 分隔行
            table_rows.append(cells)
            continue

        flush_table()

        match = _HEADING_RE.match(line)
        if match:
            blocks.append(("heading", (len(match.group(1)), clean_inline(match.group(2)))))
            continue

        if line == "---":
            blocks.append(("rule", None))
            continue

        if line.startswith("> "):
            blocks.append(("quote", clean_inline(line[2:])))
            continue

        blocks.append(("para", clean_inline(line)))

    flush_table()
    flush_code()
    return blocks


# ============================================================
# 字体
# ============================================================

# Windows 常见中文字体，按优先级探测
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/simsun.ttc",   # 宋体
    "C:/Windows/Fonts/simhei.ttf",   # 黑体
    "C:/Windows/Fonts/msyh.ttc",     # 微软雅黑
    "C:/Windows/Fonts/simfang.ttf",  # 仿宋
    "C:/Windows/Fonts/simkai.ttf",   # 楷体
]


def find_cjk_font() -> str | None:
    for candidate in _FONT_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


# ============================================================
# DOCX
# ============================================================


def _ensure_docx():
    from docx import Document

    return Document


def _add_docx_rule(doc) -> None:
    """插入一条真正的水平分隔线（段落下边框）。

    原实现写 ``doc.add_paragraph("─" * 60)`` —— 60 个制表符式方块字符会被
    当成正文文本，既是"多余的字符"，复制出去也是垃圾。改用段落边框。
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    para = doc.add_paragraph()
    p_pr = para._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:color"), "999999")
    borders.append(bottom)
    p_pr.append(borders)


def md_to_docx(md_path: Path, docx_path: Path) -> None:
    from docx.shared import Cm, Pt, RGBColor

    Document = _ensure_docx()
    doc = Document()

    style = doc.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(11)

    for kind, payload in parse_md_blocks(md_path.read_text(encoding="utf-8")):
        if kind == "heading":
            level, text = payload
            doc.add_paragraph(text, style=f"Heading {min(level, 3)}")

        elif kind == "para":
            doc.add_paragraph(payload)

        elif kind == "quote":
            para = doc.add_paragraph(payload)
            para.paragraph_format.left_indent = Cm(1)
            for run in para.runs:
                run.italic = True
                run.font.color.rgb = RGBColor(100, 100, 100)

        elif kind == "rule":
            _add_docx_rule(doc)

        elif kind == "code":
            for line in payload.split("\n"):
                para = doc.add_paragraph(line)
                para.paragraph_format.left_indent = Cm(1)
                for run in para.runs:
                    run.font.name = "Consolas"
                    run.font.size = Pt(9)

        elif kind == "table":
            rows = payload
            width = max(len(r) for r in rows)
            table = doc.add_table(rows=len(rows), cols=width)
            table.style = "Table Grid"
            for r, row_cells in enumerate(rows):
                for c in range(width):
                    cell = table.cell(r, c)
                    cell.text = row_cells[c] if c < len(row_cells) else ""
                    for para in cell.paragraphs:
                        for run in para.runs:
                            run.font.size = Pt(9)
                            run.bold = r == 0

    doc.save(str(docx_path))
    print(f"  DOCX → {docx_path.name}")


# ============================================================
# PDF
# ============================================================


def _ensure_fpdf():
    """返回 FPDF 类。缺失时安装 —— 刻意放在函数内，保证导入本模块无副作用。"""
    try:
        from fpdf import FPDF

        return FPDF
    except ImportError:
        print("安装 fpdf2 ...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "fpdf2", "-q"])
        from fpdf import FPDF

        return FPDF


def _render_pdf_table(pdf, rows: list[list[str]], font_name: str, FontFace) -> None:
    from fpdf.enums import TableBordersLayout

    pdf.set_font(font_name, "", 9)
    with pdf.table(
        width=pdf.epw,
        line_height=pdf.font_size * 1.75,
        text_align="LEFT",
        borders_layout=TableBordersLayout.ALL,
        first_row_as_headings=True,
        headings_style=FontFace(emphasis="BOLD", fill_color=(238, 238, 238)),
    ) as table:
        for row_cells in rows:
            row = table.row()
            for cell in row_cells:
                row.cell(cell)


def md_to_pdf(md_path: Path, pdf_path: Path) -> None:
    from fpdf.fonts import FontFace

    FPDF = _ensure_fpdf()

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    font_path = find_cjk_font()
    if font_path:
        pdf.add_font("CJK", "", font_path)
        pdf.add_font("CJK", "B", font_path)
        font_name = "CJK"
    else:
        print("⚠️ 未找到中文字体文件，PDF 中文可能显示为方块")
        font_name = "Helvetica"

    pdf.add_page()

    for kind, payload in parse_md_blocks(md_path.read_text(encoding="utf-8")):
        if kind == "heading":
            level, text = payload
            size = {1: 18, 2: 14, 3: 12}.get(level, 11)
            pdf.set_font(font_name, "B", size)
            pdf.multi_cell(0, size * 0.62, text, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(3)

        elif kind == "para":
            pdf.set_font(font_name, "", 10)
            pdf.multi_cell(0, 5.6, payload)
            pdf.ln(1.5)

        elif kind == "quote":
            pdf.set_font(font_name, "", 9)
            pdf.set_text_color(90, 90, 90)
            pdf.set_x(pdf.l_margin + 5)
            pdf.multi_cell(0, 5, payload)
            pdf.set_text_color(0, 0, 0)
            pdf.ln(2)

        elif kind == "rule":
            pdf.set_draw_color(160, 160, 160)
            pdf.set_line_width(0.3)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(4)

        elif kind == "code":
            pdf.set_font(font_name, "", 8.5)
            pdf.set_fill_color(245, 245, 245)
            for line in payload.split("\n"):
                pdf.set_x(pdf.l_margin + 3)
                pdf.multi_cell(0, 4.8, line, fill=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2.5)

        elif kind == "table":
            _render_pdf_table(pdf, payload, font_name, FontFace)
            pdf.ln(3)

    pdf.output(str(pdf_path))
    print(f"  PDF  → {pdf_path.name}")


# ============================================================
# Main
# ============================================================


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把 sample-docs 下的 MD 转成 PDF 与 DOCX")
    parser.add_argument("--only", default=None, help="只转换文件名含该子串的 md")
    args = parser.parse_args(argv)

    OUT.mkdir(parents=True, exist_ok=True)

    md_files = sorted(SRC.glob("*.md"))
    if args.only:
        md_files = [p for p in md_files if args.only in p.name]
    if not md_files:
        print("未找到 MD 文件")
        return 1

    print(f"找到 {len(md_files)} 个 MD 文件，输出目录 {OUT}\n")
    for md_path in md_files:
        print(f"处理: {md_path.name}")
        md_to_docx(md_path, OUT / f"{md_path.stem}.docx")
        md_to_pdf(md_path, OUT / f"{md_path.stem}.pdf")

    print(f"\n完成。MD {len(md_files)} / DOCX {len(list(OUT.glob('*.docx')))} / PDF {len(list(OUT.glob('*.pdf')))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
