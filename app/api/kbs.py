"""知识库 CRUD 路由（B3）：创建、列表、详情、删除。

所有接口需要登录。用户只能操作自己的知识库。
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, get_db
from app.core.models import KnowledgeBase, User

logger = logging.getLogger("app.kbs")
router = APIRouter(prefix="/api/kbs", tags=["kbs"])


# ---- Pydantic schemas ----

class KBCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)


class KBResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    created_at: datetime
    document_count: int = 0


class DocumentBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    file_type: str
    status: str
    chunk_count: int
    created_at: datetime


class KBDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    created_at: datetime
    document_count: int = 0
    documents: list[DocumentBrief] = []


# ---- helpers ----

def _to_kb_response(kb: KnowledgeBase) -> KBResponse:
    return KBResponse(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        created_at=kb.created_at,
        document_count=len(kb.documents) if kb.documents else 0,
    )


def _to_kb_detail(kb: KnowledgeBase) -> KBDetailResponse:
    return KBDetailResponse(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        created_at=kb.created_at,
        document_count=len(kb.documents) if kb.documents else 0,
        documents=[DocumentBrief.model_validate(d) for d in kb.documents],
    )


def _get_user_kb_or_403(db: Session, kb_id: int, user: User) -> KnowledgeBase:
    """获取知识库；不存在返回 404，属于别人返回 403。"""
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    if kb.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问该知识库")
    return kb


# ---- endpoints ----

@router.post("", response_model=KBResponse, status_code=status.HTTP_201_CREATED)
def create_kb(
    req: KBCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KBResponse:
    """创建知识库（归属当前用户）。同名不冲突（不同用户的库独立）。"""
    kb = KnowledgeBase(
        name=req.name,
        description=req.description,
        user_id=current_user.id,
    )
    db.add(kb)
    db.flush()
    return _to_kb_response(kb)


@router.get("", response_model=list[KBResponse])
def list_kbs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[KBResponse]:
    """列出当前用户的所有知识库。"""
    kbs = (
        db.query(KnowledgeBase)
        .options(selectinload(KnowledgeBase.documents))
        .filter(KnowledgeBase.user_id == current_user.id)
        .order_by(KnowledgeBase.created_at.desc())
        .all()
    )
    return [_to_kb_response(k) for k in kbs]


@router.get("/{kb_id}", response_model=KBDetailResponse)
def get_kb(
    kb_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KBDetailResponse:
    """获取单个知识库详情（含文档列表）。"""
    kb = _get_user_kb_or_403(db, kb_id, current_user)
    return _to_kb_detail(kb)


@router.delete("/{kb_id}")
def delete_kb(
    kb_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """删除知识库（级联删除文档+切片+向量）。"""
    kb = _get_user_kb_or_403(db, kb_id, current_user)

    # 删除向量集合
    try:
        from app.config import get_settings
        from app.storage.vector_store import build_vector_store

        vs = build_vector_store(get_settings())
        vs.delete_collection(kb_id)
    except Exception:
        logger.exception("删除向量集合失败 kb_id=%s", kb_id)

    db.delete(kb)
    return {"status": "deleted"}
