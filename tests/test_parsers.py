"""``app/ingestion/parsers.py`` 的解析测试。

这里的断言不是「格式好看」，而是**切片里不能出现检索噪声**：
``#``、``|``、``---``、``**`` 这些标记会进入 ``chunks.content``，
被向量与关键词两路同时命中，最后被模型抄进答案。

清理是**两段式**的，测试也按两段写：

- 解析阶段（``parse_file``）：剥表格/列表/行内/围栏标记。**标题的 ``#`` 刻意保留**，
  因为切分器靠它认章节边界。
- 切分之后（``strip_heading_marks`` / ``clean_chunks``）：才剥标题前缀，
  且只对 markdown 来源剥。

同时钉死一条边界：``.txt`` 是纯文本，里面的 ``#`` 就是字面字符，
不能被当成 markdown 标题处理。
"""
from __future__ import annotations

import re

import pytest

from app.ingestion.parsers import clean_chunks, parse_file, strip_heading_marks


# ---- helpers ----

def _write(tmp_path, name: str, text: str):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ============================================================
# 1. 标题标记：解析阶段保留、切分之后才剥
# ============================================================

@pytest.mark.parametrize("level", ["#", "##", "######"])
def test_markdown_heading_prefix_kept_at_parse_stage(tmp_path, level):
    """`#` 必须活着走出解析器 —— 切分器靠它认章节边界（chunker._RE_MD_HEADING）。

    这里曾经写错：解析阶段就把 `#` 剥掉，结果没有「第X章」/「1.2」编号的
    纯文字标题（`# 差旅报销制度`）不被任何规则识别，相邻两节被切进同一块。
    """
    text = parse_file(_write(tmp_path, "a.md", f"{level} 标题文字\n\n正文。"))
    assert f"{level} 标题文字" in text
    assert "正文。" in text


@pytest.mark.parametrize("level", ["#", "##", "######"])
def test_strip_heading_marks_removes_prefix(tmp_path, level):
    text = strip_heading_marks(parse_file(_write(tmp_path, "a.md", f"{level} 标题文字")))
    assert "标题文字" in text
    assert "#" not in text


def test_strip_heading_marks_multiline():
    assert strip_heading_marks("# 一\n\n正文\n\n## 二") == "一\n\n正文\n\n二"


def test_strip_heading_marks_keeps_hash_without_space():
    """``#话题`` 不是 ATX 标题，剥掉 ``#`` 是丢数据。"""
    assert strip_heading_marks("#话题标签") == "#话题标签"


def test_clean_chunks_strips_for_markdown_only():
    """``.txt`` 与 ``.pdf`` 的文本里行首 ``#`` 是字面字符，不能剥。"""
    raw = ["# 标题\n\n正文"]
    assert clean_chunks(raw, ".md") == ["标题\n\n正文"]
    assert clean_chunks(raw, ".markdown") == ["标题\n\n正文"]
    assert clean_chunks(raw, ".txt") == raw
    assert clean_chunks(raw, ".pdf") == raw
    assert clean_chunks(raw, ".docx") == raw


def test_clean_chunks_keeps_heading_text():
    """剥的是 `#` 前缀，标题文字本身要留下 —— 章节名是有信息量的。"""
    assert clean_chunks(["# 2.3 供应商编码规则"], ".md") == ["2.3 供应商编码规则"]


def test_clean_chunks_drops_blank_blocks():
    """整块变空串时丢掉 —— 空串送去 embed 没意义。"""
    assert clean_chunks(["", "   ", "正文"], ".md") == ["正文"]


def test_markdown_hash_without_space_is_not_heading(tmp_path):
    """``#没有空格`` 不是合法 ATX 标题，``#`` 是字面字符，不能丢。"""
    text = parse_file(_write(tmp_path, "a.md", "#没有空格"))
    assert "#没有空格" in text
    assert strip_heading_marks(text) == "#没有空格"


def test_markdown_heading_keeps_text_content(tmp_path):
    text = parse_file(_write(tmp_path, "a.md", "# 全球优选 · 员工手册\n\n正文一段。"))
    assert "全球优选 · 员工手册" in text
    assert "正文一段。" in text


# ============================================================
# 2. 表格
# ============================================================

def test_markdown_table_separator_line_dropped(tmp_path):
    """`|---|---|` 是纯语法，必须丢掉 —— 它没有任何语义，却会稀释向量。"""
    md = "| 版本 | 日期 |\n|---|---|\n| v1.0 | 2024-01-01 |"
    text = parse_file(_write(tmp_path, "a.md", md))
    assert "---" not in text
    assert "|" not in text


def test_markdown_table_cells_kept(tmp_path):
    md = "| 文件编号 | FIN-EXP-2026-003 |\n|---|---|\n| 版本号 | v2.4 |"
    text = parse_file(_write(tmp_path, "a.md", md))
    for token in ("文件编号", "FIN-EXP-2026-003", "版本号", "v2.4"):
        assert token in text


def test_markdown_alignment_colon_separator_dropped(tmp_path):
    md = "| 左 | 中 | 右 |\n|:---|:---:|---:|\n| a | b | c |"
    text = parse_file(_write(tmp_path, "a.md", md))
    assert "|" not in text
    assert "---" not in text
    for token in ("左", "中", "右", "a", "b", "c"):
        assert token in text


# ============================================================
# 3. 列表
# ============================================================

@pytest.mark.parametrize("marker", ["-", "*", "+"])
def test_markdown_unordered_list_marker_stripped(tmp_path, marker):
    text = parse_file(_write(tmp_path, "a.md", f"{marker} 出差补贴按日计发"))
    assert "出差补贴按日计发" in text
    assert marker not in text.split("出差补贴")[0]


def test_markdown_ordered_list_keeps_number(tmp_path):
    """有序序号是流程语义（第几步），去掉会丢信息，所以保留。"""
    md = "1. 核对报关单电子信息\n2. 录入出口退税申报系统"
    text = parse_file(_write(tmp_path, "a.md", md))
    assert "1. 核对报关单电子信息" in text
    assert "2. 录入出口退税申报系统" in text


# ============================================================
# 4. 行内标记
# ============================================================

def test_markdown_bold_stripped_but_text_kept(tmp_path):
    text = parse_file(_write(tmp_path, "a.md", "**质量优先**是核心原则。"))
    assert "质量优先" in text
    assert "*" not in text


def test_markdown_italic_stripped_but_text_kept(tmp_path):
    text = parse_file(_write(tmp_path, "a.md", "参考*供应商管理制度*执行。"))
    assert "供应商管理制度" in text
    assert "*" not in text


def test_markdown_inline_code_backtick_stripped(tmp_path):
    text = parse_file(_write(tmp_path, "a.md", "命令为 `uvicorn app.main:app`。"))
    assert "uvicorn app.main:app" in text
    assert "`" not in text


def test_markdown_strikethrough_stripped(tmp_path):
    text = parse_file(_write(tmp_path, "a.md", "~~旧条款~~新条款生效。"))
    assert "旧条款" in text
    assert "~" not in text


def test_markdown_link_keeps_label_drops_url(tmp_path):
    text = parse_file(_write(tmp_path, "a.md", "见[员工手册](https://example.com/hr)。"))
    assert "员工手册" in text
    assert "example.com" not in text
    assert "]" not in text


# ============================================================
# 5. 代码块
# ============================================================

def test_markdown_fence_markers_dropped_content_kept(tmp_path):
    md = "公式如下：\n\n```\n退税额 = 离岸价 × 汇率 × 退税率\n```\n\n以上。"
    text = parse_file(_write(tmp_path, "a.md", md))
    assert "退税额 = 离岸价 × 汇率 × 退税率" in text
    assert "```" not in text


def test_markdown_fence_with_language_tag(tmp_path):
    md = "```python\nprint(1)\n```"
    text = parse_file(_write(tmp_path, "a.md", md))
    assert "print(1)" in text
    assert "```" not in text
    assert "python" not in text


def test_markdown_fence_content_not_inline_processed(tmp_path):
    """围栏内的 `*` 是代码字面量，不该被当成斜体标记删掉。"""
    md = "```\nSUP-*LTH*-2026-003\n```"
    text = parse_file(_write(tmp_path, "a.md", md))
    assert "SUP-*LTH*-2026-003" in text


def test_markdown_fence_language_tag_not_leaked(tmp_path):
    md = "```json\n{\"kb\": 1}\n```"
    text = parse_file(_write(tmp_path, "a.md", md))
    assert '{"kb": 1}' in text
    assert "json" not in text


# ============================================================
# 6. 引用与水平线
# ============================================================

def test_markdown_blockquote_marker_stripped(tmp_path):
    text = parse_file(_write(tmp_path, "a.md", "> 注：德国站特殊，详见 6.3 节。"))
    assert "注：德国站特殊" in text
    assert ">" not in text


@pytest.mark.parametrize("rule", ["---", "***", "___", "- - -"])
def test_markdown_horizontal_rule_dropped(tmp_path, rule):
    md = f"上一段。\n\n{rule}\n\n下一段。"
    text = parse_file(_write(tmp_path, "a.md", md))
    assert "上一段。" in text
    assert "下一段。" in text
    assert rule not in text
    assert "*" not in text
    assert "_" not in text


# ============================================================
# 7. 必须原样保留的符号（与 scripts/generate_sample_docs.py 同口径）
# ============================================================

@pytest.mark.parametrize("symbol", ["→", "≥", "≤", "Δ", "—", "·", "°", "㎡", "±", "×"])
def test_markdown_keeps_meaningful_symbols(tmp_path, symbol):
    text = parse_file(_write(tmp_path, "a.md", f"数值 {symbol} 保留"))
    assert symbol in text


def test_markdown_does_not_eat_digits_around_symbols(tmp_path):
    md = "注入 95°C 水 6 小时后低于 55°C，色差大于 ΔE 3.0。"
    text = parse_file(_write(tmp_path, "a.md", md))
    assert "95°C" in text
    assert "55°C" in text
    assert "ΔE 3.0" in text


# ============================================================
# 8. .txt 不能走 markdown 分支（边界）
# ============================================================

def test_txt_keeps_hash_as_literal(tmp_path):
    """纯文本里的 `#` 就是字面字符 —— 当成标题去掉是丢数据。"""
    text = parse_file(_write(tmp_path, "a.txt", "# 这不是标题"))
    assert "# 这不是标题" in text


def test_txt_keeps_pipe_as_literal(tmp_path):
    text = parse_file(_write(tmp_path, "a.txt", "a | b | c"))
    assert "a | b | c" in text


# ============================================================
# 9. 结构性质
# ============================================================

def test_markdown_normalize_drops_blank_lines(tmp_path):
    text = parse_file(_write(tmp_path, "a.md", "# 标题\n\n\n\n正文\n\n\n"))
    assert "\n\n\n" not in text


def test_markdown_empty_file_returns_empty(tmp_path):
    assert parse_file(_write(tmp_path, "a.md", "")) == ""


def test_markdown_only_syntax_returns_empty(tmp_path):
    """整份都是语法、没有内容时，不该吐出 `---` 之类的残渣。"""
    text = parse_file(_write(tmp_path, "a.md", "---\n\n***\n\n| --- | --- |\n"))
    assert text.strip() == ""


def test_markdown_output_is_idempotent(tmp_path):
    """解析结果再解析一次应不变 —— 保证切片文本不会因二次处理而漂移。"""
    md = "# 标题\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- 项目\n\n**粗体**与*斜体*"
    once = parse_file(_write(tmp_path, "a.md", md))
    twice = parse_file(_write(tmp_path, "b.md", once))
    assert once == twice


# ============================================================
# 10. 端到端：真实语料不留标记
# ============================================================

def test_real_corpus_markdown_has_no_residue(tmp_path):
    """拿真实语料走完两段清理 —— 这是当初发现问题的场景（kb_2 的 md 文档整份带标记）。

    断言的是**最终入库文本**：``clean_chunks`` 之后才算数，只测 ``parse_file``
    会漏掉标题前缀那一段（那是刻意保留的）。
    """
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "docs" / "sample-docs"
    mds = sorted(src.glob("*.md"))
    assert mds, "sample-docs 下没有 md 语料"
    for md in mds:
        text = "".join(clean_chunks([parse_file(md)], md.suffix))
        assert "|" not in text, f"{md.name} 残留表格竖线"
        assert "```" not in text, f"{md.name} 残留代码围栏"
        assert "**" not in text, f"{md.name} 残留粗体标记"
        assert "---" not in text, f"{md.name} 残留水平线"
        assert not re.search(r"^#{1,6}\s", text, re.M), f"{md.name} 残留标题前缀"


def test_real_corpus_markdown_keeps_key_numbers(tmp_path):
    """清理不能顺手吃掉数值 —— 员工手册的年假天数、退税率都是必考项。"""
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "docs" / "sample-docs"
    hr = src / "全球优选_员工手册.md"
    assert hr.is_file(), "员工手册语料不存在"
    text = "".join(clean_chunks([parse_file(hr)], hr.suffix))
    for token in ("7 天", "10 天", "12 天", "15 天", "6 个月", "30 日", "16 学时"):
        assert token in text, f"清理后丢了 {token}"


def test_unsupported_extension_raises(tmp_path):
    # `.xlsx` 自 K10 起受支持（走 openpyxl）；真正不支持的扩展名仍拒绝
    with pytest.raises(ValueError):
        parse_file(_write(tmp_path, "a.doc", "x"))


def test_corrupt_xlsx_fails_loudly(tmp_path):
    """损坏的 .xlsx（非 zip 结构）→ openpyxl 抛错 → 摄取标记 failed，不静默吞。"""
    with pytest.raises(Exception):
        parse_file(_write(tmp_path, "broken.xlsx", "this is not a zip archive"))


# ---- Excel（K10）----

from openpyxl import Workbook


def _make_xlsx(tmp_path, name: str = "t.xlsx", sheets: dict | None = None):
    """用 openpyxl 生成真实 xlsx（不是文本伪造），sheet 名 → 二维行。"""
    p = tmp_path / name
    wb = Workbook()
    first = True
    for title, rows in (sheets or {}).items():
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        for row in rows:
            ws.append(row)
        first = False
    wb.save(p)
    return p


def test_parse_xlsx_sheet_headings_and_rows(tmp_path):
    """sheet 名成为小节标题；行转「列值 | 列值」；数字保持 Excel 显示原值。"""
    p = _make_xlsx(tmp_path, sheets={
        "差旅标准": [["城市", "上限"], ["一线城市", 600], ["二线城市", 450]],
        "售后": [["项目", "时效"], ["退货", 30]],
    })
    text = parse_file(p)
    assert "## 差旅标准" in text and "## 售后" in text
    assert "一线城市 | 600" in text
    assert "退货 | 30" in text


def test_parse_xlsx_skips_empty_rows_and_cells(tmp_path):
    """空行跳过、空单元格剔除——切片里只剩有信息量的内容。"""
    p = _make_xlsx(tmp_path, sheets={"s": [[None, None], ["a", None, "b"], [None]]})
    text = parse_file(p)
    lines = [l for l in text.splitlines() if l.strip()]
    assert lines == ["## s", "a | b"]


def test_parse_xlsx_clean_chunks_strips_sheet_heading(tmp_path):
    """``## sheet`` 前缀在切分后剥掉（bugfix-log #41 同款教训：## 不能残留进切片）。"""
    p = _make_xlsx(tmp_path, sheets={"售后": [["项目", "时效"], ["退货", 30]]})
    text = parse_file(p)
    cleaned = clean_chunks([text], ".xlsx")
    # _normalize 用空行分段：sheet 标题与行之间是 \n\n（不影响切分器的行首标题识别）
    assert cleaned == ["售后\n\n项目 | 时效\n\n退货 | 30"]


def test_parse_xlsx_registered_in_supported_exts():
    from app.ingestion.parsers import SUPPORTED_EXTS

    assert ".xlsx" in SUPPORTED_EXTS
