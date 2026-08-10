"""将 sample-docs 目录下的 MD 文件转换为 PDF 和 DOCX。

已装: python-docx, 需要: fpdf2
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # scripts/ 的上一级即项目根
SRC = ROOT / "docs" / "sample-docs"
OUT = ROOT / "docs" / "sample-docs"

# 确保输出目录存在
OUT.mkdir(parents=True, exist_ok=True)

# ---- install fpdf2 if missing ----
try:
    from fpdf import FPDF
except ImportError:
    print("安装 fpdf2 ...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "fpdf2", "-q"])
    from fpdf import FPDF

from docx import Document
from docx.shared import Pt, Inches, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.style import WD_STYLE_TYPE

# ---- register Chinese font ----
# 尝试查找系统中文字体
import glob as _glob

# Manually list common Windows Chinese font paths
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/simsun.ttc",        # 宋体
    "C:/Windows/Fonts/simhei.ttf",        # 黑体
    "C:/Windows/Fonts/msyh.ttc",          # 微软雅黑
    "C:/Windows/Fonts/msyhbd.ttc",        # 微软雅黑 Bold
    "C:/Windows/Fonts/simfang.ttf",       # 仿宋
    "C:/Windows/Fonts/simkai.ttf",        # 楷体
]

_FONT_PATH = None
for fp in _FONT_CANDIDATES:
    if os.path.exists(fp):
        _FONT_PATH = fp
        print(f"使用字体: {fp}")
        break

if not _FONT_PATH:
    print("⚠️ 未找到中文字体文件，PDF 中文可能显示为方块")


# ---- helper ----
def _read_md(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _title_from_path(path: Path) -> str:
    first = path.read_text(encoding="utf-8").split("\n")[0]
    return first.lstrip("# ").strip()


# ============================================================
# DOCX Generator
# ============================================================
def md_to_docx(md_path: Path, docx_path: Path) -> None:
    text = _read_md(md_path)
    lines = text.split("\n")

    doc = Document()

    # 设置默认字体
    style = doc.styles["Normal"]
    font = style.font
    font.name = "Microsoft YaHei"
    font.size = Pt(11)

    for line in lines:
        stripped = line.strip()

        # 空行
        if not stripped:
            doc.add_paragraph("")
            continue

        # 标题
        if stripped.startswith("# "):
            p = doc.add_paragraph()
            run = p.add_run(stripped[2:])
            run.bold = True
            run.font.size = Pt(20)
            p.style = doc.styles["Heading 1"]
            continue

        if stripped.startswith("## "):
            p = doc.add_paragraph()
            run = p.add_run(stripped[3:])
            run.bold = True
            run.font.size = Pt(16)
            p.style = doc.styles["Heading 2"]
            continue

        if stripped.startswith("### "):
            p = doc.add_paragraph()
            run = p.add_run(stripped[4:])
            run.bold = True
            run.font.size = Pt(13)
            p.style = doc.styles["Heading 3"]
            continue

        # 水平分割线
        if stripped == "---":
            doc.add_paragraph("─" * 60)
            continue

        # 表格行 → 渲染为紧凑文本（保留内容不丢失）
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            # 跳过分隔行（全是 - 或 : 组成）
            if all(re.match(r"^[-:]+$", c) for c in cells):
                continue
            doc.add_paragraph("  |  ".join(cells))
            continue

        # 引用块
        if stripped.startswith("> "):
            p = doc.add_paragraph(stripped[2:])
            p.paragraph_format.left_indent = Cm(1)
            for run in p.runs:
                run.italic = True
                run.font.color.rgb = RGBColor(100, 100, 100)
            continue

        # 加粗
        stripped = re.sub(r"\*\*(.+?)\*\*", r"\1", stripped)

        # 普通段落
        doc.add_paragraph(stripped)

    doc.save(str(docx_path))
    print(f"  DOCX → {docx_path.name}")


# ============================================================
# PDF Generator (fpdf2)
# ============================================================
def md_to_pdf(md_path: Path, pdf_path: Path) -> None:
    text = _read_md(md_path)
    lines = text.split("\n")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    # 注册中文字体
    if _FONT_PATH:
        # fpdf2 需要区分 regular 和 bold
        # 简单处理：统一用同一个字体
        pdf.add_font("CJK", "", _FONT_PATH)
        pdf.add_font("CJK", "B", _FONT_PATH)
        FONT_NAME = "CJK"
    else:
        FONT_NAME = "Helvetica"

    pdf.add_page()

    for line in lines:
        stripped = line.strip()

        if not stripped:
            pdf.ln(5)
            continue

        # 标题
        if stripped.startswith("# "):
            pdf.set_font(FONT_NAME, "B", 18)
            pdf.cell(0, 12, stripped[2:], new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)
            continue

        if stripped.startswith("## "):
            pdf.set_font(FONT_NAME, "B", 14)
            pdf.cell(0, 10, stripped[3:], new_x="LMARGIN", new_y="NEXT")
            pdf.ln(3)
            continue

        if stripped.startswith("### "):
            pdf.set_font(FONT_NAME, "B", 12)
            pdf.cell(0, 8, stripped[4:], new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
            continue

        # 水平分割线
        if stripped == "---":
            pdf.set_line_width(0.5)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(4)
            continue

        # 表格行 → 渲染为紧凑文本（保留内容不丢失）
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            # 跳过分隔行（全是 - 或 : 组成）
            if all(re.match(r"^[-:]+$", c) for c in cells):
                continue
            pdf.set_font(FONT_NAME, "", 9)
            pdf.multi_cell(0, 5, "  |  ".join(cells))
            pdf.ln(1)
            continue

        # 引用块
        if stripped.startswith("> "):
            pdf.set_font(FONT_NAME, "", 9)
            pdf.set_text_color(100, 100, 100)
            x = pdf.get_x()
            pdf.cell(5, 5, "")  # 缩进
            pdf.multi_cell(0, 5, stripped[2:])
            pdf.set_text_color(0, 0, 0)
            pdf.ln(2)
            continue

        # 加粗处理
        stripped = re.sub(r"\*\*(.+?)\*\*", r"\1", stripped)

        # 普通段落
        pdf.set_font(FONT_NAME, "", 10)
        # 处理超长行
        if len(stripped) > 120:
            pdf.multi_cell(0, 5.5, stripped)
            pdf.ln(1)
        else:
            pdf.cell(0, 6, stripped, new_x="LMARGIN", new_y="NEXT")

    pdf.output(str(pdf_path))
    print(f"  PDF  → {pdf_path.name}")


# ============================================================
# Main
# ============================================================
def main():
    md_files = sorted(SRC.glob("*.md"))
    if not md_files:
        print("未找到 MD 文件")
        return

    print(f"找到 {len(md_files)} 个 MD 文件，开始转换...\n")

    for md_path in md_files:
        stem = md_path.stem
        print(f"处理: {md_path.name}")

        md_to_docx(md_path, OUT / f"{stem}.docx")
        md_to_pdf(md_path, OUT / f"{stem}.pdf")

    print(f"\n✅ 完成！输出目录: {OUT}")
    print(f"   MD  文件: {len(md_files)}")
    print(f"   DOCX 文件: {len(list(OUT.glob('*.docx')))}")
    print(f"   PDF  文件: {len(list(OUT.glob('*.pdf')))}")


if __name__ == "__main__":
    main()
