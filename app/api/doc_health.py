"""文档健康分析 API（G5）：死文档 + 命中热力。

基于 G4 问答日志（qa_logs.hit_doc_ids）聚合每个文档的命中次数与最近命中时间：
- 从未被命中的文档 → 死文档清单（带 status/chunk_count/error，供前端区分未就绪与真死角）
- 命中过的文档 → 热力排行 top N

纯元数据查询，不动向量库。鉴权复用 _get_user_kb_or_403。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.chat import _parse_doc_ids
from app.api.deps import get_current_user, get_db
from app.api.kbs import _get_user_kb_or_403
from app.core.models import Document, QaLog, User

router = APIRouter(prefix="/api/kbs", tags=["doc-health"])


# ---- schemas ----

class DeadDoc(BaseModel):
    """从未被问答命中的文档。"""

    id: int
    filename: str
    status: str
    chunk_count: int
    hit_count: int
    error: str | None = None


class HotDoc(BaseModel):
    """至少命中过一次的文档（热力排行项）。"""

    id: int
    filename: str
    hit_count: int
    last_hit_at: datetime | None = None


class DocHealthSummary(BaseModel):
    total_documents: int
    indexed: int
    active_count: int  # 至少命中一次的文档数
    dead_count: int  # 从未命中（含未就绪文档）
    total_questions: int  # 该 KB 问答日志条数
    hit_rate: float  # active_count / total_documents


class DocHealthResponse(BaseModel):
    kb_id: int
    summary: DocHealthSummary
    dead_docs: list[DeadDoc]
    hot_docs: list[HotDoc]


# ---- 共享聚合（G5/G6 一个真相源）----

def compute_hit_stats(
    db: Session, kb_id: int
) -> tuple[list[Document], dict[int, int], dict[int, datetime], int]:
    """KB 的文档清单 + 每文档被问答命中的次数与最近命中时间。

    从 qa_logs.hit_doc_ids（JSON 数组）聚合；日志按时间升序遍历，后写覆盖即最新。
    返回 ``(docs, hit_counts, last_hits, total_logs)``——数据看板（G6）复用本函数，
    不复制命中统计逻辑。
    """
    docs = db.query(Document).filter(Document.kb_id == kb_id).all()
    hit_counts: dict[int, int] = {}
    last_hits: dict[int, datetime] = {}
    logs = (
        db.query(QaLog)
        .filter(QaLog.kb_id == kb_id)
        .order_by(QaLog.created_at.asc())
        .all()
    )
    for log in logs:
        for doc_id in _parse_doc_ids(log.hit_doc_ids):
            hit_counts[doc_id] = hit_counts.get(doc_id, 0) + 1
            last_hits[doc_id] = log.created_at
    return docs, hit_counts, last_hits, len(logs)


# ---- endpoint ----

@router.get("/{kb_id}/doc-health", response_model=DocHealthResponse)
def get_kb_doc_health(
    kb_id: int,
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DocHealthResponse:
    """文档健康分析：死文档清单 + 命中热力 top N。"""
    _get_user_kb_or_403(db, kb_id, current_user)

    docs, hit_counts, last_hits, total_logs = compute_hit_stats(db, kb_id)

    total_documents = len(docs)
    indexed = sum(1 for d in docs if d.status == "indexed")
    active_count = sum(1 for d in docs if hit_counts.get(d.id, 0) > 0)
    dead_count = total_documents - active_count

    dead_docs = [
        DeadDoc(
            id=d.id,
            filename=d.filename,
            status=d.status,
            chunk_count=d.chunk_count or 0,
            hit_count=0,
            error=d.error,
        )
        for d in docs
        if hit_counts.get(d.id, 0) == 0
    ]
    hot_docs = sorted(
        (
            HotDoc(
                id=d.id,
                filename=d.filename,
                hit_count=hit_counts[d.id],
                last_hit_at=last_hits.get(d.id),
            )
            for d in docs
            if hit_counts.get(d.id, 0) > 0
        ),
        key=lambda h: (h.hit_count, h.last_hit_at or datetime.min),
        reverse=True,
    )[:limit]

    return DocHealthResponse(
        kb_id=kb_id,
        summary=DocHealthSummary(
            total_documents=total_documents,
            indexed=indexed,
            active_count=active_count,
            dead_count=dead_count,
            total_questions=total_logs,
            hit_rate=round(active_count / total_documents, 2) if total_documents else 0.0,
        ),
        dead_docs=dead_docs,
        hot_docs=hot_docs,
    )
