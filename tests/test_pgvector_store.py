"""PgVectorStore 集成测试（A10）。

仅当 PostgreSQL 可达时运行，否则整模块跳过，保证离线/CI 仍可跑。
普通测试走 chroma（见 A10 需求），本文件专门验证 pgvector 代码路径，
避免「代码改了但从未被执行」导致的静默回归。

验证点：ensure_collection 建表 → add 写入（upsert）→ search 检索 →
kb 隔离 → delete_document → delete_collection。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app.config import Settings
from app.providers.factory import build_embedding
from app.storage.pgvector_store import PgVectorStore
from app.storage.vector_store import ChunkToIndex

# 测试专用 kb_id，避免与真实数据（demo kb#1）冲突
KB_1 = 900_001
KB_2 = 900_002


def _pg_reachable(settings: Settings) -> bool:
    """尝试连接 PostgreSQL，失败说明环境不可用（跳过而非失败）。

    注意 `create_engine` 与 `connect` 要分开处理：前者只在导入 DBAPI 时失败，
    那是**依赖缺陷**（如 #43 的 psycopg 缺失），必须让测试红掉；后者才是
    "本机没起 PG"的环境问题。若合成一个宽泛的 except，驱动缺失会被伪装成
    skip，pgvector 整条代码路径就此静默失去覆盖。
    """
    try:
        engine = create_engine(settings.database_url)
    except (ImportError, ModuleNotFoundError) as exc:
        pytest.fail(f"postgresql 驱动不可用，检查 pyproject 依赖声明：{exc}")

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def settings() -> Settings:
    s = Settings(vector_store="pgvector")
    if not _pg_reachable(s):
        pytest.skip("PostgreSQL 不可达，跳过 pgvector 集成测试")
    return s


def _one_hot(index: int, dim: int) -> list[float]:
    """第 index 位为 1、其余为 0 的单位向量，用于确定性相似度。"""
    return [1.0 if j == index else 0.0 for j in range(dim)]


def test_add_search_delete(settings: Settings) -> None:
    # 与 IngestionPipeline 一致：建表维度取 embedding provider 的 dim（mock=64），
    # 而非 settings.embedding_dim（1024）——两者可能不同，错配会导致维度报错。
    dim = build_embedding(settings).dim
    store = PgVectorStore(settings)

    store.ensure_collection(KB_1, dim)
    store.ensure_collection(KB_2, dim)

    chunks = [
        ChunkToIndex(id=f"{KB_1}:0", kb_id=KB_1, document_id=101, chunk_index=0, content="猫和狗都是宠物", vector=_one_hot(0, dim)),
        ChunkToIndex(id=f"{KB_1}:1", kb_id=KB_1, document_id=101, chunk_index=1, content="今天是晴天", vector=_one_hot(1, dim)),
        ChunkToIndex(id=f"{KB_1}:2", kb_id=KB_1, document_id=102, chunk_index=0, content="汽车在高速上行驶", vector=_one_hot(2, dim)),
    ]
    store.add(KB_1, chunks)

    # 同类向量命中对应内容
    top = store.search(KB_1, _one_hot(0, dim), top_k=1)
    assert top and top[0].content == "猫和狗都是宠物"

    # 知识库隔离：另一 kb 检索不到
    assert store.search(KB_2, _one_hot(0, dim), top_k=1) == []

    # 按文档删除后，该文档切片不可再命中，其他文档不受影响
    store.delete_document(KB_1, 101)
    remaining = store.search(KB_1, _one_hot(0, dim), top_k=5)
    assert all(s.document_id != 101 for s in remaining)
    assert any(s.document_id == 102 for s in store.search(KB_1, _one_hot(2, dim), top_k=5))

    # 清理共享表，不留测试数据
    store.delete_collection(KB_1)
    store.delete_collection(KB_2)
    assert store.search(KB_1, _one_hot(0, dim), top_k=1) == []


def test_document_counts(settings: Settings) -> None:
    """document_counts 分组计数正确，kb 无数据/清理后为空（G3）。"""
    dim = build_embedding(settings).dim
    store = PgVectorStore(settings)
    KB_3 = 900_003

    store.ensure_collection(KB_3, dim)
    store.add(
        KB_3,
        [
            ChunkToIndex(id=f"{KB_3}:0", kb_id=KB_3, document_id=901, chunk_index=0, content="a", vector=_one_hot(0, dim)),
            ChunkToIndex(id=f"{KB_3}:1", kb_id=KB_3, document_id=901, chunk_index=1, content="b", vector=_one_hot(1, dim)),
            ChunkToIndex(id=f"{KB_3}:2", kb_id=KB_3, document_id=902, chunk_index=0, content="c", vector=_one_hot(2, dim)),
        ],
    )
    assert store.document_counts(KB_3) == {901: 2, 902: 1}

    # 表存在但该 kb 无数据 → 空 dict
    assert store.document_counts(999_999) == {}

    store.delete_collection(KB_3)
    assert store.document_counts(KB_3) == {}


def test_search_lexical(settings: Settings) -> None:
    """H1：关键词全文检索命中含词的切片，ts_rank 降序；无可用词返回空列表。"""
    dim = build_embedding(settings).dim
    store = PgVectorStore(settings)
    KB = 900_010

    store.ensure_collection(KB, dim)
    store.add(
        KB,
        [
            ChunkToIndex(id=f"{KB}:0", kb_id=KB, document_id=801, chunk_index=0, content="型号 GPT-4 工业级设备说明", vector=_one_hot(0, dim)),
            ChunkToIndex(id=f"{KB}:1", kb_id=KB, document_id=801, chunk_index=1, content="无关文本介绍", vector=_one_hot(1, dim)),
        ],
    )

    hits = store.search_lexical(KB, "gpt-4", top_k=5)
    assert hits and hits[0].chunk_index == 0
    assert hits[0].score > 0
    # 无可用词（单字符）→ 空列表，避免 to_tsquery 报错
    assert store.search_lexical(KB, "a", top_k=5) == []

    store.delete_collection(KB)


def test_migration_idempotent(settings: Settings) -> None:
    """H1：表已建时重复 ensure_collection 幂等——ALTER 加列、回填、GIN 索引不重复。"""
    from sqlalchemy import create_engine, text

    dim = build_embedding(settings).dim
    store = PgVectorStore(settings)
    KB = 900_011

    store.ensure_collection(KB, dim)
    PgVectorStore._table_ready = False  # 强制重跑迁移路径
    try:
        store.ensure_collection(KB, dim)
    finally:
        PgVectorStore._table_ready = True

    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT indexname FROM pg_indexes WHERE indexname = 'ix_vectors_fts_gin'")
        ).fetchall()
    assert len(rows) == 1  # GIN 全文索引只有一份

    store.delete_collection(KB)
