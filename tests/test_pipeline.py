"""全链路测试（mock 供应商，无网络/密钥）：
摄取两主题文档 → 针对不同主题提问 → 验证检索命中正确主题 + 答案与来源。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.core.models import Document
from app.ingestion.pipeline import IngestionPipeline
from app.providers.factory import build_embedding, build_llm, build_reranker
from app.rag.pipeline import RagPipeline
from app.storage.db import init_db
from app.storage.vector_store import build_vector_store

DOC_TEXT = """# 测试知识库

## 差旅报销制度

出差住宿标准：一线城市每晚不超过六百元，其他城市每晚不超过四百元。交通费用凭票据实报销，市内交通费按每天五十元标准包干。餐饮补贴：一线城市每天一百二十元，其他城市每天九十元，出差补贴随当月工资发放。

## 年假管理制度

年假按员工入职年限计算：入职满一年享五天，满三年享八天，满五年享十天，满十年享十五天。员工申请年假须提前三个工作日在 OA 系统中提交申请，经直属上级审批通过后方可休假。
"""


@pytest.fixture()
def env(tmp_path: Path) -> tuple[Settings, sessionmaker, IngestionPipeline, RagPipeline]:
    """隔离环境：mock 供应商 + 临时 data 目录。"""
    settings = Settings(
        rag_provider="mock",
        data_dir=tmp_path / "data",
        chunk_size=200,
        chunk_overlap=20,
        top_k=3,
    )
    _, session_factory = init_db(settings)
    vector_store = build_vector_store(settings)
    embedding = build_embedding(settings)
    llm = build_llm(settings)
    reranker = build_reranker(settings)

    ingest = IngestionPipeline(
        settings=settings,
        session_factory=session_factory,
        vector_store=vector_store,
        embedding=embedding,
    )
    rag = RagPipeline(
        settings=settings,
        session_factory=session_factory,
        vector_store=vector_store,
        embedding=embedding,
        llm=llm,
        reranker=reranker,
    )
    return settings, session_factory, ingest, rag


def _write_doc(tmp_path: Path) -> Path:
    p = tmp_path / "policy.md"
    p.write_text(DOC_TEXT, encoding="utf-8")
    return p


def test_full_chain_mock(env, tmp_path: Path) -> None:
    settings, session_factory, ingest, rag = env
    kb = ingest.create_kb("测试库")
    assert kb.id is not None

    path = _write_doc(tmp_path)
    doc = ingest.ingest_file(kb.id, path)
    assert doc.status == "indexed"
    assert doc.chunk_count >= 2
    assert doc.file_type == "md"

    # 主题一：报销
    r1 = rag.ask(kb.id, "出差住宿标准是多少钱？")
    assert r1.answer  # MockLLM 返回模板答案
    assert r1.sources and "报销" in r1.sources[0].content

    # 主题二：年假
    r2 = rag.ask(kb.id, "年假有几天？")
    assert r2.sources and "年假" in r2.sources[0].content

    # 来源附带文档名
    assert r1.sources[0].filename == "policy.md"
    assert r1.sources[0].document_id == doc.id


def test_query_empty_kb(env) -> None:
    _, _, ingest, rag = env
    kb = ingest.create_kb("空库")
    result = rag.ask(kb.id, "随便问什么")
    assert "未找到" in result.answer
    assert result.sources == []


def test_document_metadata_persisted(env, tmp_path: Path) -> None:
    _, session_factory, ingest, _ = env
    kb = ingest.create_kb("元数据库")
    ingest.ingest_file(kb.id, _write_doc(tmp_path))

    with session_factory() as db:
        docs = db.query(Document).all()
        assert len(docs) == 1
        assert docs[0].status == "indexed"
        assert docs[0].chunk_count == docs[0].chunks.__len__()
