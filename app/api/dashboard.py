"""数据看板（G6）：把 G4 问答日志、K5 反馈、K6 缓存命中聚合成一页可决策的数字。

- 只读、viewer+ 可读（复用 `_get_user_kb_or_403`）；
- 聚合全部走现成三张表（qa_logs / query_cache / message_feedback），不建新表；
- 死文档统计复用 G5 的 `compute_hit_stats`（一个真相源，不复制逻辑）；
- 一个端点一次返回全部块，前端不发零散请求；趋势图刻意不做（见需求卡片）。
"""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.doc_health import compute_hit_stats
from app.api.kbs import _get_user_kb_or_403
from app.core.models import (
    ChatMessage,
    Conversation,
    MessageFeedback,
    QueryCache,
    QaLog,
    User,
)
from app.rag.query_cache import normalize_query

router = APIRouter(prefix="/api/kbs", tags=["dashboard"])

_TOP_LIMIT = 10


# ---- schemas ----

class TopQuestion(BaseModel):
    query: str
    count: int  # qa_logs 中的提问次数
    cache_hits: int  # 该问法在 query_cache 中的累计命中（未缓存为 0）


class ReasonCount(BaseModel):
    reason: str
    count: int


class DeadDocItem(BaseModel):
    filename: str
    status: str


class DashboardOut(BaseModel):
    kb_id: int
    # ---- 概览统计卡 ----
    total_questions: int
    cache_hits: int
    cache_hit_rate: float  # cache_hits / total_questions（含多轮等不缓存提问，诚实口径）
    feedback_total: int
    feedback_up: int
    feedback_up_rate: float  # up / total
    total_documents: int
    dead_documents: int
    # ---- 榜单 ----
    top_questions: list[TopQuestion]
    reason_breakdown: list[ReasonCount]
    dead_doc_list: list[DeadDocItem]


# ---- 聚合片段（全部只读，互不依赖）----

def _count(db: Session, column_query) -> int:
    return column_query.scalar() or 0


def _question_stats(db: Session, kb_id: int) -> tuple[int, list[TopQuestion]]:
    """累计问答 + 常问问题 TOP N（缓存命中按规范化问法求和对齐）。"""
    total = _count(db, db.query(func.count(QaLog.id)).filter(QaLog.kb_id == kb_id))
    rows = (
        db.query(QaLog.query, func.count(QaLog.id).label("c"))
        .filter(QaLog.kb_id == kb_id)
        .group_by(QaLog.query)
        .order_by(func.count(QaLog.id).desc(), QaLog.query)
        .limit(_TOP_LIMIT)
        .all()
    )
    # 同一问法可能因 mode/top_k/model 不同存在多条缓存 → 命中数按 norm 求和
    cache_map: dict[str, int] = defaultdict(int)
    norms = [normalize_query(q) for q, _ in rows]
    if norms:
        for norm, hc in (
            db.query(QueryCache.query_norm, QueryCache.hit_count)
            .filter(QueryCache.kb_id == kb_id, QueryCache.query_norm.in_(norms))
            .all()
        ):
            cache_map[norm] += hc or 0
    top = [
        TopQuestion(query=q, count=c, cache_hits=cache_map.get(normalize_query(q), 0))
        for q, c in rows
    ]
    return total, top


def _cache_stats(db: Session, kb_id: int) -> tuple[int, int]:
    hits = _count(
        db,
        db.query(func.coalesce(func.sum(QueryCache.hit_count), 0)).filter(
            QueryCache.kb_id == kb_id
        ),
    )
    entries = _count(db, db.query(func.count(QueryCache.id)).filter(QueryCache.kb_id == kb_id))
    return hits, entries


def _feedback_stats(
    db: Session, kb_id: int
) -> tuple[int, int, list[ReasonCount]]:
    """反馈总数 / 有用数 / 「没用」原因分布（feedback 无 kb_id，经消息两跳过滤）。"""
    base = (
        db.query(MessageFeedback)
        .join(ChatMessage, MessageFeedback.message_id == ChatMessage.id)
        .join(Conversation, ChatMessage.conversation_id == Conversation.id)
        .filter(Conversation.kb_id == kb_id)
    )
    total = base.count()
    up = base.filter(MessageFeedback.rating == "up").count()
    reasons = (
        db.query(MessageFeedback.reason, func.count(MessageFeedback.id).label("c"))
        .join(ChatMessage, MessageFeedback.message_id == ChatMessage.id)
        .join(Conversation, ChatMessage.conversation_id == Conversation.id)
        .filter(
            Conversation.kb_id == kb_id,
            MessageFeedback.rating == "down",
            MessageFeedback.reason.isnot(None),
        )
        .group_by(MessageFeedback.reason)
        .order_by(func.count(MessageFeedback.id).desc())
        .all()
    )
    return total, up, [ReasonCount(reason=r, count=c) for r, c in reasons]


# ---- endpoint ----

@router.get("/{kb_id}/dashboard", response_model=DashboardOut)
def get_dashboard(
    kb_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DashboardOut:
    """数据看板聚合：概览统计 + 常问问题 TOP + 反馈原因分布 + 死文档。"""
    _get_user_kb_or_403(db, kb_id, current_user)

    total_questions, top_questions = _question_stats(db, kb_id)
    cache_hits, _entries = _cache_stats(db, kb_id)
    feedback_total, feedback_up, reason_breakdown = _feedback_stats(db, kb_id)
    docs, hit_counts, _last, _total_logs = compute_hit_stats(db, kb_id)

    dead = [d for d in docs if hit_counts.get(d.id, 0) == 0]
    total_documents = len(docs)

    return DashboardOut(
        kb_id=kb_id,
        total_questions=total_questions,
        cache_hits=cache_hits,
        cache_hit_rate=round(cache_hits / total_questions, 2) if total_questions else 0.0,
        feedback_total=feedback_total,
        feedback_up=feedback_up,
        feedback_up_rate=round(feedback_up / feedback_total, 2) if feedback_total else 0.0,
        total_documents=total_documents,
        dead_documents=len(dead),
        top_questions=top_questions,
        reason_breakdown=reason_breakdown,
        dead_doc_list=[
            DeadDocItem(filename=d.filename, status=d.status) for d in dead[:_TOP_LIMIT]
        ],
    )
