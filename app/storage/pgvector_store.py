"""PgVectorStore：基于 PostgreSQL + pgvector 的向量库实现。

单表存储所有知识库向量（kb_id 分区），通过 pgvector 的 <=> 余弦距离算子检索。
"""

from __future__ import annotations

from pgvector import Vector as PgVector

from app.config import Settings, get_settings
from app.rag.hybrid import tokenize
from app.storage.vector_store import ChunkToIndex, ScoredChunk, VectorStore


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
        """惰性创建 engine，避免 import 时就连数据库。"""
        if self._engine is None:
            from sqlalchemy import create_engine

            self._engine = create_engine(self._settings.database_url)
        return self._engine

    def _ensure_ext(self, dim: int) -> None:
        """确保 pgvector 扩展、向量表与类型适配器就绪（幂等，仅首次执行）。"""
        if PgVectorStore._table_ready:
            return

        import pgvector.psycopg2

        with self.engine.connect() as conn:
            conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
            pgvector.psycopg2.register_vector(conn.connection.driver_connection, globally=True)
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