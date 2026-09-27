"""语料生成脚本：markdown 标记与 emoji 不得留进 PDF。

**背景（真实缺陷，已修）**

``scripts/generate_sample_docs.py`` 原实现把 md 逐行扫一遍，只在**普通段落**分支里
执行 ``re.sub(r"\\*\\*(.+?)\\*\\*", ...)``；**表格行**与**引用行**分支在拼接完就
``continue``，跳过了那次清理，而**单星号斜体**从头到尾没人处理。

后果不是"看起来丑"，而是**进了向量库**：实测
``docs/sample-docs/全球优选_跨境售后政策.pdf`` 共 2622 字，含 **30 个字面星号**，
检索出来的切片长这样::

    美国  |  因州而异（多数无强制）  |  **30 天无理由退货**  |  FTC Mail Order Rule

星号会随切片一起进 prompt，模型可能直接抄进答案。

**修法**：抽出两个纯函数，让所有分支走同一条清理路径，从结构上堵死"绕过"——

- ``clean_inline``     行内标记 + emoji → 纯文本
- ``parse_md_blocks``  md 文本 → 结构化 block 列表（标题/段落/引用/分隔线/**表格**）

两个渲染器（PDF / DOCX）只消费 block，不再各自扫行，清理逻辑因此只有一处。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "generate_sample_docs.py"
SAMPLE_DOCS = ROOT / "docs" / "sample-docs"


def _load_module():
    """按文件路径加载脚本，避免依赖 scripts/ 成为包。

    脚本已重构为**导入期无副作用**（不再在模块级 import fpdf 并在缺失时
    ``pip install``）——否则 pytest 收集阶段就会触发一次网络安装。
    """
    spec = importlib.util.spec_from_file_location("_gen_sample_docs", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


gen = _load_module()


# ---------------------------------------------------------------- clean_inline


def test_clean_inline_strips_bold_markers():
    assert gen.clean_inline("我们承诺 **30 天无理由退货** 政策") == "我们承诺 30 天无理由退货 政策"


def test_clean_inline_strips_italic_markers():
    # 单星号斜体：原实现完全没处理，PDF 里原样保留 `*`
    assert gen.clean_inline("*本文档每季度复审，更新后推送至所有客服人员企业微信。*") == (
        "本文档每季度复审，更新后推送至所有客服人员企业微信。"
    )


def test_clean_inline_strips_bold_and_italic_in_same_line():
    assert gen.clean_inline("**注意**: 德国站*特殊*——免费退货") == "注意: 德国站特殊——免费退货"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("96.2%/0.3% ✅", "96.2%/0.3%"),
        ("⚠️ 需约谈", "需约谈"),
        ("🔨 进行中", "进行中"),
        ("⬜ 待办 ❌ 失败", "待办 失败"),
        ("⚠ 缺变体选择符", "缺变体选择符"),
    ],
)
def test_clean_inline_strips_emoji(raw, expected):
    assert gen.clean_inline(raw) == expected


@pytest.mark.parametrize(
    "kept",
    [
        "客服组长 → 客服经理 → 运营总监",  # 箭头是正文，不是 emoji
        "偏差 >1cm 且 ≥2 处",              # 比较符
        "色差 >ΔE 3.0",                    # 希腊字母
        "-20°C ~ 95°C，面积 8000㎡",       # 单位
        "制造—销售—售后",                   # 全角破折号
        "文件编号: POL-CS-2026-002",        # 冒号与连字符
    ],
)
def test_clean_inline_keeps_meaningful_symbols(kept):
    """emoji 清理必须精确，误伤箭头/比较符/单位会直接改坏文档语义。"""
    assert gen.clean_inline(kept) == kept


def test_clean_inline_collapses_whitespace_left_by_emoji_removal():
    """emoji 被删掉后留下的多余空格要收掉，否则 PDF 里出现「96.2% 」这种尾随空格。"""
    assert gen.clean_inline("4PX ⚠️  需约谈") == "4PX 需约谈"
    assert "  " not in gen.clean_inline("云途 ✅ 燕文 ✅")


def test_clean_inline_is_idempotent():
    once = gen.clean_inline("**A** ⚠️ *B*")
    assert gen.clean_inline(once) == once


# ---------------------------------------------------------------- parse_md_blocks


def test_parse_md_blocks_recognises_headings():
    blocks = gen.parse_md_blocks("# 一级\n## 二级\n### 三级\n")
    assert blocks == [
        ("heading", (1, "一级")),
        ("heading", (2, "二级")),
        ("heading", (3, "三级")),
    ]


def test_parse_md_blocks_cleans_bold_inside_paragraph():
    assert gen.parse_md_blocks("**退换货承诺**: 我们负责到底。\n") == [
        ("para", "退换货承诺: 我们负责到底。")
    ]


def test_parse_md_blocks_cleans_dash_list_items():
    blocks = gen.parse_md_blocks("- 顾客提供错误尺码\n- 明显人为损坏\n")
    assert blocks == [("para", "- 顾客提供错误尺码"), ("para", "- 明显人为损坏")]


def test_parse_md_blocks_cleans_blockquote():
    assert gen.parse_md_blocks("> **注意**: 德国站特殊——免费退货\n") == [
        ("quote", "注意: 德国站特殊——免费退货")
    ]


def test_parse_md_blocks_emits_rule():
    assert gen.parse_md_blocks("---\n") == [("rule", None)]


def test_parse_md_blocks_groups_table_and_drops_separator_row():
    md = "| 市场 | 承诺 |\n|---|---|\n| 美国 | **30 天** |\n| 日本 | 14 天 |\n"
    assert gen.parse_md_blocks(md) == [
        ("table", [["市场", "承诺"], ["美国", "30 天"], ["日本", "14 天"]])
    ]


def test_parse_md_blocks_table_does_not_leak_markdown_markers():
    """回归：表格是原缺陷的发生地——单元格里的 `**` 曾被原样写进 PDF。"""
    blocks = gen.parse_md_blocks("| 市场 | 我们承诺 |\n|---|---|\n| 美国 | **30 天无理由退货** |\n")
    assert blocks[0][0] == "table"
    flat = " ".join(cell for row in blocks[0][1] for cell in row)
    assert "*" not in flat
    assert "30 天无理由退货" in flat


def test_parse_md_blocks_table_cells_keep_emoji_out():
    blocks = gen.parse_md_blocks("| 承运商 | 表现 |\n|---|---|\n| 云途 | 96.2% ✅ |\n")
    flat = " ".join(cell for row in blocks[0][1] for cell in row)
    assert "✅" not in flat
    assert "96.2%" in flat


def test_parse_md_blocks_skips_blank_lines():
    assert gen.parse_md_blocks("\n\n正文\n\n\n") == [("para", "正文")]


def test_parse_md_blocks_groups_code_fence_and_drops_markers():
    """``` 标记行不能进 PDF —— 反引号是纯粹的"多余字符"。"""
    md = "```\nSUP-{品类}-{年份}-{序号}\n品类: LTH(皮具), BTL(杯具)\n```\n"
    assert gen.parse_md_blocks(md) == [("code", "SUP-{品类}-{年份}-{序号}\n品类: LTH(皮具), BTL(杯具)")]


def test_parse_md_blocks_code_fence_does_not_leak_backticks():
    blocks = gen.parse_md_blocks("```\n订单抓取 → 审单 → 配货\n```\n")
    assert all("`" not in str(value) for _, value in blocks)


def test_parse_md_blocks_two_tables_separated_by_heading():
    md = "| a |\n|---|\n| 1 |\n\n## 下一节\n\n| b |\n|---|\n| 2 |\n"
    kinds = [k for k, _ in gen.parse_md_blocks(md)]
    assert kinds == ["table", "heading", "table"]


# ---------------------------------------------------------------- 端到端护栏


def _pdf_text(pdf_path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# emoji 区间（与脚本内保持一致的判定口径：CJK 文档里不该出现的图形符号）
_EMOJI_RE = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U00002b00-\U00002bff\ufe0f]"
)

_GENERATED_PDFS = sorted(SAMPLE_DOCS.glob("全球优选_*.pdf"))


@pytest.mark.skipif(not _GENERATED_PDFS, reason="尚未生成 PDF，先跑 scripts/generate_sample_docs.py")
@pytest.mark.parametrize("pdf_path", _GENERATED_PDFS, ids=lambda p: p.stem)
def test_generated_pdf_has_no_markdown_residue(pdf_path: Path):
    """端到端护栏：星号不得出现在 PDF 抽取文本里（原缺陷即在此被观测到）。"""
    text = _pdf_text(pdf_path)
    assert text.strip(), f"{pdf_path.name} 抽取不到文本，PDF 可能是坏的"
    assert "*" not in text, f"{pdf_path.name} 仍含 {text.count('*')} 个字面星号"


@pytest.mark.skipif(not _GENERATED_PDFS, reason="尚未生成 PDF，先跑 scripts/generate_sample_docs.py")
@pytest.mark.parametrize("pdf_path", _GENERATED_PDFS, ids=lambda p: p.stem)
def test_generated_pdf_has_no_emoji(pdf_path: Path):
    text = _pdf_text(pdf_path)
    found = set(_EMOJI_RE.findall(text))
    assert not found, f"{pdf_path.name} 仍含 emoji: {sorted(found)}"


@pytest.mark.skipif(not _GENERATED_PDFS, reason="尚未生成 PDF，先跑 scripts/generate_sample_docs.py")
def test_generated_pdf_keeps_table_content():
    """清标记不能把内容一起清掉：表格里的关键数值必须还在。"""
    target = SAMPLE_DOCS / "全球优选_跨境售后政策.pdf"
    if not target.exists():
        pytest.skip("缺该 PDF")
    text = _pdf_text(target)
    for cell in ("FTC Mail Order Rule", "EU Consumer Rights Directive", "30 天无理由退货", "日本"):
        assert cell in text, f"表格内容 {cell!r} 在 PDF 里丢了"
