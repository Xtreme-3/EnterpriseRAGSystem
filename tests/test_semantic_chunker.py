"""H2：语义切分（structure / structure+semantic）单元测试 + 摄取接入 + 配置校验。"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.ingestion.chunker import split_semantic, split_structure

CHUNK = 100
OVERLAP = 20

MD_DOC = """# 测试知识库

## 差旅报销制度

出差住宿标准：一线城市每晚不超过六百元。交通费用凭票据实报销。市内交通每天五十元。餐饮补贴每天一百二十元。出差补贴随当月工资发放。住宿发票须为增值税专用发票。住宿标准每两年调整一次。超标准部分不予报销。特殊城市经审批可上浮百分之二十。

## 年假管理制度

年假按员工入职年限计算：满一年享五天，满三年享八天。申请须提前三个工作日在 OA 提交。年假可结转至次年三月底。当年未使用完的年假自动作废。法定节假日不计入年假天数。
"""


# ---- split_structure：结构优先 ----

def test_structure_empty() -> None:
    assert split_structure("") == []
    assert split_structure("   \n\n  ") == []


def test_structure_short_text_single_chunk() -> None:
    assert split_structure("一句话。") == ["一句话。"]


def test_structure_markdown_headings_separate_sections() -> None:
    chunks = split_structure(MD_DOC, CHUNK, OVERLAP)
    # 报销与年假内容不应混在同一块
    for c in chunks:
        assert not ("报销" in c and "年假" in c), f"块混了主题: {c[:20]}"


def test_structure_heading_binds_with_content() -> None:
    # 用 200 让每个小节能整体放入一块，验证「标题与其内容同块」
    chunks = split_structure(MD_DOC, chunk_size=200, chunk_overlap=0)
    travel = [c for c in chunks if "差旅报销制度" in c][0]
    assert "六百元" in travel  # 标题与其内容在同一块


def test_structure_numbered_section_headings() -> None:
    text = (
        "1 适用范围\n\n本制度适用于全体正式员工及外派人员。\n\n"
        "2 报销标准\n\n一线城市住宿每晚不超过六百元，交通凭票实报实销，超出部分不予报销。"
    )
    chunks = split_structure(text, chunk_size=60, chunk_overlap=0)
    assert len(chunks) == 2
    assert chunks[0].startswith("1 适用范围")
    assert chunks[1].startswith("2 报销标准")


def test_structure_plain_prose_falls_back() -> None:
    """无结构信号的正文退化为递归切分：多块、块不超长、内容无丢失。"""
    text = "\n\n".join(f"第{i}段。" + "公司为员工提供五险一金与每年体检。" for i in range(8))
    chunks = split_structure(text, CHUNK, OVERLAP)
    assert len(chunks) > 1
    for c in chunks[:-1]:
        assert len(c) <= CHUNK + OVERLAP
    for i in range(8):
        assert any(f"第{i}段" in c for c in chunks)


def test_structure_oversized_section_split() -> None:
    """单一超长节（无标题分隔）按句子边界再切，不产生超长块。"""
    text = "适用范围。" + "该标准适用于所有出厂产品。" * 40
    chunks = split_structure(text, CHUNK, OVERLAP)
    assert len(chunks) > 1
    for c in chunks[:-1]:
        assert len(c) <= CHUNK + OVERLAP


def test_structure_no_content_loss() -> None:
    joined = "".join(split_structure(MD_DOC, CHUNK, OVERLAP))
    for key in ("差旅报销制度", "六百元", "年假管理制度", "满三年享八天"):
        assert key in joined


# ---- split_semantic：结构 + 句级 embedding 微调 ----

def _topic_embed():
    """假 embed：含「差旅」的句子同主题，其余异主题，用于离线验证断层切分。"""
    def embed(texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            out.append([1.0, 0.0] if "差旅" in t else [0.0, 1.0])
        return out
    return embed


TRAVEL = "差旅报销制度规定一线城市住宿标准为每晚不超过六百元，出差交通费用凭票据实报销。"
LEAVE = "年假管理制度规定入职满一年享有五天带薪年假，年假须提前三天在系统中提交申请。"


def test_semantic_splits_on_topic_shift() -> None:
    """主题断层（差旅→年假）处切开：任何块不混主题。"""
    text = "\n\n".join([TRAVEL, LEAVE] * 6)
    chunks = split_semantic(text, _topic_embed(), threshold=0.65, chunk_size=200, chunk_overlap=0)
    assert len(chunks) > 1
    for c in chunks:
        assert not ("差旅" in c and "年假" in c), f"块混了主题: {c[:20]}"


def test_semantic_no_split_high_similarity() -> None:
    """全同主题：无主题断层，只按长度打包，内容不丢。"""
    text = "\n\n".join([TRAVEL] * 10)
    chunks = split_semantic(text, _topic_embed(), threshold=0.65, chunk_size=200, chunk_overlap=0)
    assert chunks
    for c in chunks:
        assert "差旅" in c
        assert "年假" not in c


def test_semantic_threshold_controls_split() -> None:
    """阈值放松（-1）时断层不切，主题混在同块；正常阈值则切开、块更多。"""
    text = "\n\n".join([TRAVEL, LEAVE] * 8)
    high = split_semantic(text, _topic_embed(), threshold=0.65, chunk_size=200, chunk_overlap=0)
    low = split_semantic(text, _topic_embed(), threshold=-1.0, chunk_size=200, chunk_overlap=0)
    assert high and not any("差旅" in c and "年假" in c for c in high)
    assert len(low) < len(high)
    assert any("差旅" in c and "年假" in c for c in low)


# ---- 配置校验 ----

def test_chunk_strategy_validator() -> None:
    assert Settings(chunk_strategy="fixed").chunk_strategy == "fixed"
    assert Settings(chunk_strategy="structure").chunk_strategy == "structure"
    assert Settings(chunk_strategy="structure+semantic").chunk_strategy == "structure+semantic"
    with pytest.raises(Exception):
        Settings(chunk_strategy="bogus")
    assert Settings().chunk_strategy == "fixed"  # 默认保持回归兜底


# ---- 摄取接入：structure 策略可跑通 ----

def test_ingest_with_structure_strategy(tmp_path) -> None:
    """chunk_strategy=structure 时摄取走结构切分：报销节/年假节各自成块，不按长度硬切。"""
    from app.core.models import Chunk, User
    from app.ingestion.pipeline import IngestionPipeline
    from app.providers.factory import build_embedding
    from app.storage.db import init_db
    from app.storage.vector_store import build_vector_store

    settings = Settings(
        rag_provider="mock",
        data_dir=tmp_path / "data",
        chunk_size=150,
        chunk_overlap=20,
        vector_store="chroma",
        chunk_strategy="structure",
    )
    _, session_factory = init_db(settings)
    with session_factory() as db:
        user = User(username="_h2_test", hashed_password="")
        db.add(user)
        db.flush()
        user_id = user.id

    ingest = IngestionPipeline(
        settings=settings,
        session_factory=session_factory,
        vector_store=build_vector_store(settings),
        embedding=build_embedding(settings),
    )
    kb = ingest.create_kb("H2语义切分库", user_id=user_id)
    p = tmp_path / "policy.md"
    p.write_text(MD_DOC, encoding="utf-8")

    doc = ingest.ingest_file(kb.id, p)
    assert doc.status == "indexed"
    # structure：差旅节与年假节分开入库（≥2 个语义块）
    assert doc.chunk_count >= 2

    with session_factory() as db:
        chunks = [c.content for c in db.query(Chunk).filter_by(document_id=doc.id).order_by("chunk_index")]
    assert any("差旅报销制度" in c for c in chunks)
    assert any("年假管理制度" in c for c in chunks)
    assert not any("报销" in c and "年假" in c for c in chunks)
