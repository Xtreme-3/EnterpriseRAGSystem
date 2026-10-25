"""审计日志查询（L2）：本仓第一个全局（非 KB 作用域）接口，仅 admin。

记账不在这里（各写点经 ``app.core/audit.record_audit`` 落库），这里只读。
QaLog / Dashboard 是 KB 级 viewer+ 视角，审计是 admin 全局视角，两者不重叠。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_admin
from app.core.models import AuditLog, User

router = APIRouter(prefix="/api/audit-logs", tags=["audit"])


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    action: str
    actor_id: int | None = None
    actor_name: str
    kb_id: int | None = None
    target: str
    detail: str
    created_at: datetime


class AuditLogPage(BaseModel):
    items: list[AuditLogOut]
    total: int


@router.get("", response_model=AuditLogPage)
def list_audit_logs(
    action: str | None = Query(None, description="按 action 精确过滤"),
    actor_id: int | None = Query(None),
    kb_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> AuditLogPage:
    """审计流水（append-only）：按时间倒序分页，action / actor / kb 三维过滤。"""
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    if actor_id is not None:
        q = q.filter(AuditLog.actor_id == actor_id)
    if kb_id is not None:
        q = q.filter(AuditLog.kb_id == kb_id)

    total = q.count()
    rows = (
        q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return AuditLogPage(items=[AuditLogOut.model_validate(r) for r in rows], total=total)
