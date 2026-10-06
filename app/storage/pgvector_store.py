"""PgVectorStore：基于 PostgreSQL + pgvector 的向量库实现。

单表存储所有知识库向量（kb_id 分区），通过 pgvector 的 <=> 余弦距离算子检索。
"""

from __future__ import annotations

import logging
import re

from pgvector import Vector as PgVector

from app.config import Settings, get_settings
from app.rag.hybrid import tokenize
from app.storage.vector_store import ChunkToIndex, ScoredChunk, VectorStore

logger = logging.getLogger(__name__)


class PgVectorStore(VectorStore):
    """PostgreSQL + pgvector 向量库实现。

    单表 ``vectors`` 存所有知识库切片，通过 kb_id 隔离。
    """

    # 向量表名（所有知识库共用一张表）
    TABLE = "vectors"

    # 类级别初始化标记，避免每次调用 add 重复执行建表/扩展
    _table_ready = False

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._dim: int | None = None
        self._engine = None

    @property
    def engine(self):
        """惰性创建 engine，避免 import 时就连数据库。

        K2：管线单例化后连接会被长期复用，必须显式配池 ——
        ``pool_pre_ping=True`` 防止空闲过久拿到已被服务端掐断的死连接，
        池上限则避免并发增长时无限开连接。
        """
        if self._engine is None:
            from sqlalchemy import create_engine

            self._engine = create_engine(
                self._settings.database_url,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
            )
        return self._engine

    def _reconcile_dim(self, conn, dim: int) -> None:
        """让已存在的向量表维度与当前 embedding 一致（否则写入必失败）。

        背景：``embedding vector({dim})`` 的维度只在**建表**时确定，
        ``CREATE TABLE IF NOT EXISTS`` 遇到已存在的表会直接跳过 —— 于是换供应商
        （本项目的现实场景：Docker 交付用 mock 64 维、本地开发用百炼 1024 维）
        时表里还是旧维度，表现为一次写入抛
        ``DataError: expected 1024 dimensions, not 64``，
        而栈底那行完全看不出是"表结构没跟上配置"。

        处置分两种：**空表直接重建**（换维度没有任何数据要保，重建是唯一正确解，
        也不该让用户手工去敲 DDL）；**非空表报错**，因为自动 drop 会静默毁掉
        已灌进的语料 —— 这种破坏性动作必须由人决定。
        """
        existing = conn.exec_driver_sql(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            f"WHERE attrelid = to_regclass('{self.TABLE}') "
            "AND attname = 'embedding' AND NOT attisdropped"
        ).scalar()
        if not existing:  # 表还不存在 —— 正常首启路径
            return
        match = re.search(r"\((\d+)\)", existing)
        if not match or int(match.group(1)) == dim:
            return

        old_dim = int(match.group(1))
        rows = conn.exec_driver_sql(f"SELECT count(*) FROM {self.TABLE}").scalar() or 0
        if rows:
            raise RuntimeError(
                f"向量表 {self.TABLE} 的维度是 {old_dim}，而当前 embedding 输出 {dim} 维，"
                f"且表内已有 {rows} 行数据。\n"
                f"  · 若确实要换 embedding：先清掉旧向量（DROP TABLE {self.TABLE};）后重新摄取全部文档；\n"
                f"  · 若只是临时切了供应商：把 EMBEDDING_PROVIDER / EMBEDDING_DIM 改回建表时的配置。\n"
                f"（本表不做自动重建，避免静默丢弃已有语料。）"
            )
        logger.warning(
            "向量表 %s 维度由 %s 改为 %s：表内无数据，自动重建", self.TABLE, old_dim, dim
        )
        conn.exec_driver_sql(f"DROP TABLE {self.TABLE}")

    def _ensure_ext(self, dim: int) -> None:
        """确保 pgvector 扩展、向量表与类型适配器就绪（幂等，仅首次执行）。"""
        if PgVectorStore._table_ready:
            return

        import pgvector.psycopg2

        with self.engine.connect() as conn:
            conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
            pgvector.psycopg2.register_vector(conn.connection.driver_connection, globally=True)
            # 必须在 CREATE TABLE IF NOT EXISTS **之前**：维度不符的老表若不先处理掉，
            # CREATE 会静默什么都不做，错误要拖到写入时才以一句
            # `expected 1024 dimensions, not 64` 从 SQLAlchemy 栈底冒出来。
            self._reconcile_dim(conn, dim)
            conn.exec_driver_sql(
                f"""
                CREATE TABLE IF NOT EXISTS {self.TABLE} (
                    id             TEXT PRIMARY KEY,
                    kb_id          INTEGER NOT NULL,
                    document_id    INTEGER NOT NULL,
                    chunk_index    INTEGER NOT NULL,
                    content        TEXT NOT NULL,
                    content_tokens TEXT NOT NULL DEFAULT '',
                    embedding      vector({dim}) NOT NULL
                )
                """
            )
            _ensure_index(conn, self.TABLE, "ix_vectors_kb_id", "kb_id")
            _ensure_index(conn, self.TABLE, "ix_vectors_document_id", "document_id")

            # ---- H1 混合检索：全文检索列迁移（老表加列 + 回填 + GIN 索引，幂等） ----
            conn.exec_driver_sql(
                f"ALTER TABLE {self.TABLE} ADD COLUMN IF NOT EXISTS content_tokens TEXT NOT NULL DEFAULT ''"
            )
            self._backfill_tokens(conn)
            conn.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS ix_vectors_fts_gin "
                f"ON {self.TABLE} USING GIN (to_tsvector('simple', content_tokens))"
            )
            conn.commit()

        PgVectorStore._table_ready = True

    def _backfill_tokens(self, conn) -> None:
        """为已存在但 content_tokens 为空的行补上分词（老库迁移用）。"""
        from sqlalchemy import text

        rows = conn.execute(
            text(f"SELECT id, content FROM {self.TABLE} WHERE content_tokens = ''")
        ).fetchall()
        for r in rows:
            toks = " ".join(tokenize(r.content))
            if toks:
                conn.execute(
                    text(f"UPDATE {self.TABLE} SET content_tokens = :t WHERE id = :id"),
                    {"t": toks, "id": r.id},
                )

    # ---- VectorStore 接口 ----

    def ensure_collection(self, kb_id: int, dim: int) -> None:
        self._ensure_ext(dim)

    def add(self, kb_id: int, chunks: list[ChunkToIndex]) -> None:
        if not chunks:
            return
        self._ensure_ext(len(chunks[0].vector))

        from sqlalchemy import text

        # 批量 INSERT，单次 SQL 往返，避免逐条写入的性能问题
        placeholders = ", ".join(
            f"(:id_{i}, :kb_id_{i}, :doc_id_{i}, :chunk_{i}, :content_{i}, :tokens_{i}, :vec_{i})"
            for i in range(len(chunks))
        )
        params: dict[str, object] = {}
        for i, c in enumerate(chunks):
            params.update({
                f"id_{i}": c.id,
                f"kb_id_{i}": kb_id,
                f"doc_id_{i}": c.document_id,
                f"chunk_{i}": c.chunk_index,
                f"content_{i}": c.content,
                f"tokens_{i}": " ".join(tokenize(c.content)),  # H1 全文检索分词
                f"vec_{i}": PgVector(c.vector),
            })

        with self.engine.connect() as conn:
            conn.execute(
                text(
                    f"""
                    INSERT INTO {self.TABLE} (id, kb_id, document_id, chunk_index, content, content_tokens, embedding)
                    VALUES {placeholders}
                    ON CONFLICT (id) DO UPDATE SET
                        content        = EXCLUDED.content,
                        content_tokens = EXCLUDED.content_tokens,
                        embedding      = EXCLUDED.embedding
                    """
                ),
                params,
            )
            conn.commit()

    def search(self, kb_id: int, vector: list[float], top_k: int) -> list[ScoredChunk]:
        from sqlalchemy import text

        # 确保 pgvector 类型适配器已注册（新进程直接问答时不可少，避免 can't adapt type 'Vector'）
        self._ensure_ext(len(vector))

        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT document_id, kb_id, chunk_index, content,
                           1 - (embedding <=> :vec) AS similarity
                    FROM {self.TABLE}
                    WHERE kb_id = :kb_id
                      AND (1 - (embedding <=> :vec)) > :min_score  -- 低于命中阈值的无关向量视为不命中
                    ORDER BY embedding <=> :vec
                    LIMIT :top_k
                    """
                ),
                {"vec": PgVector(vector), "kb_id": kb_id, "top_k": max(top_k, 1),
                 "min_score": self._settings.similarity_threshold},
            ).fetchall()

        return [
            ScoredChunk(
                document_id=r.document_id,
                kb_id=r.kb_id,
                chunk_index=r.chunk_index,
                content=r.content,
                score=float(r.similarity),
            )
            for r in rows
        ]

    def search_lexical(self, kb_id: int, query: str, top_k: int) -> list[ScoredChunk]:
        """关键词全文检索（H1）：tsvector @@ OR-tsquery，按 ts_rank 降序。

        查询词经 tokenize 与入库侧同词空间；无可用词返回空列表（避免 to_tsquery 报错）。
        """
        terms = tokenize(query)
        if not terms:
            return []
        tsq = " | ".join(terms)

        from sqlalchemy import text

        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    f"""
                    SELECT document_id, kb_id, chunk_index, content,
                           ts_rank(to_tsvector('simple', content_tokens),
                                   to_tsquery('simple', :q)) AS score
                    FROM {self.TABLE}
                    WHERE kb_id = :kb_id
                      AND to_tsvector('simple', content_tokens) @@ to_tsquery('simple', :q)
                    ORDER BY score DESC, chunk_index ASC
                    LIMIT :top_k
                    """
                ),
                {"q": tsq, "kb_id": kb_id, "top_k": max(top_k, 1)},
            ).fetchall()

        return [
            ScoredChunk(
                document_id=r.document_id,
                kb_id=r.kb_id,
                chunk_index=r.chunk_index,
                content=r.content,
                score=float(r.score),
            )
            for r in rows
        ]

    def delete_document(self, kb_id: int, document_id: int) -> None:
        from sqlalchemy import text

        with self.engine.connect() as conn:
            conn.execute(
                text(f"DELETE FROM {self.TABLE} WHERE kb_id = :kb_id AND document_id = :doc_id"),
                {"kb_id": kb_id, "doc_id": document_id},
            )
            conn.commit()

    def delete_collection(self, kb_id: int) -> None:
        from sqlalchemy import text

        with self.engine.connect() as conn:
            conn.execute(
                text(f"DELETE FROM {self.TABLE} WHERE kb_id = :kb_id"),
                {"kb_id": kb_id},
            )
            conn.commit()

    def document_counts(self, kb_id: int) -> dict[int, int]:
        from sqlalchemy import text
        from sqlalchemy.exc import ProgrammingError

        try:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    text(
                        f"""
                        SELECT document_id, COUNT(*) AS cnt
                        FROM {self.TABLE}
                        WHERE kb_id = :kb_id
                        GROUP BY document_id
                        """
                    ),
                    {"kb_id": kb_id},
                ).fetchall()
        except ProgrammingError:
            # 表未建（从未摄取过任何文档）：视为无向量，避免体检接口 500
            return {}
        return {r.document_id: int(r.cnt) for r in rows}


def _ensure_index(conn, table: str, index_name: str, column: str) -> None:
    """幂等创建 B-tree 索引（PG 不支持 IF NOT EXISTS 语法，手写检查）。"""
    from sqlalchemy import text

    exists = conn.execute(
        text("SELECT 1 FROM pg_indexes WHERE indexname = :name"),
        {"name": index_name},
    ).fetchone()
    if exists is None:
        conn.exec_driver_sql(f"CREATE INDEX {index_name} ON {table} ({column})")