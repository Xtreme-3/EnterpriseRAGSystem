"""chunker 单元测试：边界、尺寸约束、内容覆盖、重叠连续性。"""
from __future__ import annotations

import pytest

from app.ingestion.chunker import split_text

CHUNK = 100
OVERLAP = 20


def _long_text(unit: str, repeat: int = 6) -> str:
    """用多个中文段落拼成长文本，保证超过 chunk_size。"""
    return "\n\n".join(f"第{i}段。" + unit for i in range(repeat))


def test_empty_and_whitespace() -> None:
    assert split_text("") == []
    assert split_text("   \n\n  ") == []


def test_short_text_single_chunk() -> None:
    text = "一句话。"
    assert split_text(text, CHUNK, OVERLAP) == [text]


def test_chunk_size_bound() -> None:
    text = _long_text("公司为员工提供五险一金、补充商业医疗保险与每年一次的健康体检，福利内容详见员工手册福利章节。")
    chunks = split_text(text, CHUNK, OVERLAP)
    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        assert len(chunk) <= CHUNK
    # 最后一块不截断，可能超过 CHUNK 但不超过 CHUNK + OVERLAP
    assert len(chunks[-1]) <= CHUNK + OVERLAP
    for chunk in chunks:
        assert chunk.strip()


def test_no_content_loss() -> None:
    text = _long_text("考勤制度规定工作时间为周一至周五上午九点到下午六点。")
    chunks = split_text(text, CHUNK, OVERLAP)
    assert len(chunks) > 1
    # 每段开头都应出现在某个 chunk 中
    for i in range(6):
        assert any(f"第{i}段" in chunk for chunk in chunks)


def test_consecutive_chunks_share_overlap() -> None:
    text = _long_text("报销需要提交发票与报销单，抬头须为公司全称。")
    chunks = split_text(text, CHUNK, OVERLAP)
    assert len(chunks) > 1
    for i in range(1, len(chunks)):
        prev_tail = chunks[i - 1][-OVERLAP:]
        assert chunks[i].startswith(prev_tail), f"chunk{i} 应以 chunk{i-1} 尾部开头"


def test_hard_split_very_long_word() -> None:
    # 无标点无空格的超长串，应走字符硬切且不产生空块
    text = "字" * 500
    chunks = split_text(text, CHUNK, OVERLAP)
    assert len(chunks) > 1
    assert all(chunks)


@pytest.mark.parametrize("chunk_size", [50, 100, 300])
def test_different_sizes(chunk_size: int) -> None:
    text = _long_text("信息安全制度要求员工不得将内部资料带离办公环境。")
    overlap = chunk_size // 5
    chunks = split_text(text, chunk_size, overlap)
    assert chunks
    # 除最后一块外，其余块 <= chunk_size；最后一块因为不截断以保留尾部内容，可能 <= chunk_size + overlap
    for c in chunks[:-1]:
        assert len(c) <= chunk_size
    if len(chunks) > 1:
        assert len(chunks[-1]) <= chunk_size + overlap
