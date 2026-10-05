"""问答缓存（K6）：kb 内问题 → 答案的两级缓存（精确 + 语义）。

设计决策（2026-10-05，grill-me 五问定案）：

- 挂在 ``RagPipeline`` 上（K2：随 lifespan 装配一次，共用同一 ``session_factory``）；
- **只缓存单轮提问**：history 非空的多轮请求一律不查不写 —— 改写结果依赖会话上下文，
  缓存键会爆炸且收益低（由调用方 pipeline 判定，本组件不管）；
- 精确命中键 = ``(kb_id, query_norm, mode, top_k)``；语义命中 = 同 kb 同 mode 下
  现算**查询**向量与条目 embedding 的余弦 ≥ ``CACHE_SEMANTIC_THRESHOLD``；
- 失效是 **kb 级全量**：文档一有增删即清空该库（documents API 挂钩；删库走 FK 级联）
  —— 宁可少命中，绝不给旧答案；
- 不做 TTL；容量上限 LRU（``updated_at`` 最旧先淘汰）；
- 本组件只抛异常，不吞：调用方（pipeline）统一 try/except + warning，
  保证缓存任何故障都不影响问答主链路。
"""
from __future__ import annotations

import json
import logging
import math
import struct
from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

from app.config import Settings
# 本模块自己也有一个叫 QueryCache 的类（缓存组件）—— ORM 模型必须起别名，
# 否则模块级定义会把导入遮蔽掉，db.query(QueryCache) 拿到的就不是表模型。
from app.core.models import QueryCache as QueryCacheRow
from app.core.models import utcnow
from app.storage.db import get_db

logger = logging.getLogger(__name__)


def normalize_query(query: str) -> str:
    """精确命中键的规范化：折叠连续空白 + 去首尾空白 + lower。

    刻意**不做**更多（去标点 / 分词 / 同义词）——键的规范化越激进，
    "不同问题拿到同一条缓存"的风险越大；激进的相似度判断交给语义层。
    """
    return " ".join(query.split()).strip().lower()


def pack_embedding(vec: list[float]) -> bytes:
    """float32 紧凑打包（1024 维 ≈ 4KB/条，BLOB 落库）。"""
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_embedding(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


@dataclass
class CachedAnswer:
    """一次缓存命中的载荷。``sources`` 是 SourceRef 字段的 dict 列表，
    由调用方（pipeline）转回 SourceRef —— 本模块不依赖 rag 类型，避免循环导入。"""

    answer: str
    sources: list[dict] = field(default_factory=list)
    rewritten_query: str = ""
    citation_issues: list[int] = field(default_factory=list)
    score: float = 1.0  # 语义命中时的余弦分；精确命中 = 1.0
    entry_id: int = 0
    query: str = ""  # 缓存条目的原始问句（命中日志对照用）


class QueryCache:
    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker,
        embedding,  # EmbeddingProvider：语义命中现算查询向量 / 写入时给条目算向量
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.embedding = embedding

    @property
    def enabled(self) -> bool:
        return self.settings.cache_enabled

    # ---- 查 ----

    def lookup(
        self, kb_id: int, query: str, mode: str, top_k: int, model: str
    ) -> CachedAnswer | None:
        """两级查找：先精确（键全等），未中且开启语义层再做余弦匹配。

        语义匹配同样限定同 mode + top_k + model —— 只对**问法**宽容，
        参数不同（条数 / 模型切换）一律 miss，否则参数会被缓存静默吞掉。
        """
        norm = normalize_query(query)
        with get_db(self.session_factory) as db:
            row = (
                db.query(QueryCacheRow)
                .filter(
                    QueryCacheRow.kb_id == kb_id,
                    QueryCacheRow.query_norm == norm,
                    QueryCacheRow.mode == mode,
                    QueryCacheRow.top_k == top_k,
                    QueryCacheRow.model == model,
                )
                .first()
            )
            if row is not None:
                self._touch(db, row)
                logger.info("问答缓存精确命中 kb_id=%s query=%r", kb_id, query)
                return self._to_cached(row, score=1.0)

            if not self.settings.cache_semantic:
                return None

            entries = (
                db.query(QueryCacheRow)
                .filter(
                    QueryCacheRow.kb_id == kb_id,
                    QueryCacheRow.mode == mode,
                    QueryCacheRow.top_k == top_k,
                    QueryCacheRow.model == model,
                )
                .all()
            )
            entries = [e for e in entries if e.embedding is not None]

        # 网络调用（embed）放在数据库会话之外
        if not entries:
            return None
        qvec = self.embedding.embed([query])[0]

        best, best_score = None, -1.0
        for e in entries:
            score = cosine(qvec, unpack_embedding(e.embedding))
            if score > best_score:
                best, best_score = e, score

        if best is None or best_score < self.settings.cache_semantic_threshold:
            return None

        with get_db(self.session_factory) as db:
            row = db.query(QueryCacheRow).filter(QueryCacheRow.id == best.id).first()
            if row is None:
                return None
            self._touch(db, row)
            logger.info(
                "问答缓存语义命中 kb_id=%s score=%.4f 查询=%r 命中条目=%r",
                kb_id, best_score, query, row.query,
            )
            return self._to_cached(row, score=best_score)

    # ---- 写 ----

    def store(
        self,
        *,
        kb_id: int,
        query: str,
        mode: str,
        top_k: int,
        model: str,
        answer: str,
        sources: list[dict],
        rewritten_query: str = "",
        citation_issues: list[int] | None = None,
    ) -> None:
        """upsert 一条缓存：键已存在则整体覆盖（同键重答以最新为准），
        随后按容量上限做 LRU 裁剪。"""
        norm = normalize_query(query)
        vec = pack_embedding(self.embedding.embed([query])[0])
        sources_json = json.dumps(sources, ensure_ascii=False)
        issues_json = json.dumps(citation_issues or [], ensure_ascii=False)

        with get_db(self.session_factory) as db:
            row = (
                db.query(QueryCacheRow)
                .filter(
                    QueryCacheRow.kb_id == kb_id,
                    QueryCacheRow.query_norm == norm,
                    QueryCacheRow.mode == mode,
                    QueryCacheRow.top_k == top_k,
                    QueryCacheRow.model == model,
                )
                .first()
            )
            if row is None:
                row = QueryCacheRow(
                    kb_id=kb_id,
                    query_norm=norm,
                    query=query,
                    mode=mode,
                    top_k=top_k,
                )
                db.add(row)
            row.answer = answer
            row.sources = sources_json
            row.rewritten_query = rewritten_query
            row.citation_issues = issues_json
            row.model = model
            row.embedding = vec
            row.updated_at = utcnow()
            db.flush()

            self._evict_lru(db, kb_id)

    def _evict_lru(self, db, kb_id: int) -> None:
        """容量上限：超出 cache_max_entries 的部分按 updated_at 最旧淘汰。
        刚写入的条目 updated_at 最新，不会被选中。"""
        limit = self.settings.cache_max_entries
        if limit <= 0:
            return
        count = (
            db.query(func.count(QueryCacheRow.id))
            .filter(QueryCacheRow.kb_id == kb_id)
            .scalar()
        )
        extra = int(count or 0) - limit
        if extra <= 0:
            return
        stale = (
            db.query(QueryCacheRow)
            .filter(QueryCacheRow.kb_id == kb_id)
            .order_by(QueryCacheRow.updated_at.asc())
            .limit(extra)
            .all()
        )
        for row in stale:
            db.delete(row)
        logger.info("问答缓存 LRU 淘汰 kb_id=%s 淘汰=%s 条", kb_id, len(stale))

    # ---- 失效 ----

    def invalidate_kb(self, kb_id: int) -> int:
        """文档增删后调用：清空该库全部缓存，返回清除条数。"""
        with get_db(self.session_factory) as db:
            deleted = (
                db.query(QueryCacheRow).filter(QueryCacheRow.kb_id == kb_id).delete()
            )
        if deleted:
            logger.info("问答缓存按库失效 kb_id=%s 清除=%s 条", kb_id, deleted)
        return deleted

    # ---- 内部 ----

    def _touch(self, db, row: QueryCacheRow) -> None:
        """命中计数 + LRU 时钟（updated_at）。"""
        row.hit_count = (row.hit_count or 0) + 1
        row.updated_at = utcnow()

    def _to_cached(self, row: QueryCacheRow, *, score: float) -> CachedAnswer:
        return CachedAnswer(
            answer=row.answer,
            sources=json.loads(row.sources or "[]"),
            rewritten_query=row.rewritten_query or "",
            citation_issues=json.loads(row.citation_issues or "[]"),
            score=score,
            entry_id=row.id,
            query=row.query,
        )
