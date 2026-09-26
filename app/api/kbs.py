"""知识库 CRUD 路由（B3）：创建、列表、详情、删除。
I2 RBAC：新增成员角色判定与成员管理路由。

所有接口需要登录。访问权限按角色（owner/editor/viewer）判定。
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_current_user, get_db, get_rag
from app.core.models import KnowledgeBase, KnowledgeBaseMember, User

logger = logging.getLogger("app.kbs")
router = APIRouter(prefix="/api/kbs", tags=["kbs"])

# I2 RBAC：角色等级（级别越高权限越大）。viewer 只读；editor 可改文档；owner 可删库/管成员。
ROLE_LEVEL = {"viewer": 1, "editor": 2, "owner": 3}
MEMBER_ROLES = ("editor", "viewer")  # 成员可被授予的角色（owner 不入成员表，固定为创建者）


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
    # I2 RBAC：当前请求用户对此库的角色（owner | editor | viewer）
    role: str = "viewer"


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
    role: str = "viewer"


class MemberOut(BaseModel):
    user_id: int
    username: str
    role: str  # owner | editor | viewer
    created_at: datetime | None = None


class MemberAddRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    role: str = Field(..., description="editor | viewer")

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in MEMBER_ROLES:
            raise ValueError(f"role 必须是 {' | '.join(MEMBER_ROLES)}")
        return v


class MemberRoleRequest(BaseModel):
    role: str = Field(..., description="editor | viewer")

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in MEMBER_ROLES:
            raise ValueError(f"role 必须是 {' | '.join(MEMBER_ROLES)}")
        return v


# ---- helpers ----

def _to_kb_response(kb: KnowledgeBase, role: str) -> KBResponse:
    return KBResponse(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        created_at=kb.created_at,
        document_count=len(kb.documents) if kb.documents else 0,
        role=role,
    )


def _to_kb_detail(kb: KnowledgeBase, role: str) -> KBDetailResponse:
    return KBDetailResponse(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        created_at=kb.created_at,
        document_count=len(kb.documents) if kb.documents else 0,
        documents=[DocumentBrief.model_validate(d) for d in kb.documents],
        role=role,
    )


def get_user_kb_role(db: Session, kb: KnowledgeBase, user: User) -> str | None:
    """返回用户对该知识库的角色：owner | editor | viewer；无访问权限返回 None。

    - admin 视为任意库的 owner（全局旁路）
    - owner = KnowledgeBase.user_id（创建者，权威来源，不入成员表）
    - 协作成员查 KnowledgeBaseMember
    """
    if user.role == "admin":
        return "owner"
    if kb.user_id == user.id:
        return "owner"
    member = (
        db.query(KnowledgeBaseMember)
        .filter(
            KnowledgeBaseMember.kb_id == kb.id,
            KnowledgeBaseMember.user_id == user.id,
        )
        .first()
    )
    return member.role if member else None


def _get_user_kb_or_403(
    db: Session, kb_id: int, user: User, required: str = "viewer"
) -> KnowledgeBase:
    """获取知识库（须有访问权限）。不存在返回 404，无权限/权限不足返回 403。

    ``required``：viewer | editor | owner，默认 viewer（读接口零改动即用）。
    """
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    role = get_user_kb_role(db, kb, user)
    if role is None:
        raise HTTPException(status_code=403, detail="无权访问该知识库")
    if ROLE_LEVEL[role] < ROLE_LEVEL[required]:
        raise HTTPException(
            status_code=403,
            detail=f"权限不足：该操作需要 {required} 角色或更高",
        )
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
    return _to_kb_response(kb, "owner")


@router.get("", response_model=list[KBResponse])
def list_kbs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[KBResponse]:
    """列出当前用户有权访问的知识库（拥有 + 作为成员；admin 返回全部），每条带我的角色。"""
    q = (
        db.query(KnowledgeBase)
        .options(selectinload(KnowledgeBase.documents))
        .order_by(KnowledgeBase.created_at.desc())
    )
    if current_user.role == "admin":
        kbs = q.all()
    else:
        kbs = (
            q.filter(
                or_(
                    KnowledgeBase.user_id == current_user.id,
                    KnowledgeBase.id.in_(
                        select(KnowledgeBaseMember.kb_id).where(
                            KnowledgeBaseMember.user_id == current_user.id
                        )
                    ),
                )
            )
            .all()
        )
    return [
        _to_kb_response(k, get_user_kb_role(db, k, current_user) or "viewer") for k in kbs
    ]


@router.get("/{kb_id}", response_model=KBDetailResponse)
def get_kb(
    kb_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KBDetailResponse:
    """获取单个知识库详情（含文档列表）。"""
    kb = _get_user_kb_or_403(db, kb_id, current_user)
    return _to_kb_detail(kb, get_user_kb_role(db, kb, current_user) or "viewer")


@router.delete("/{kb_id}")
def delete_kb(
    kb_id: int,
    request: Request = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """删除知识库（级联删除文档+切片+向量）。仅 owner 可执行。"""
    kb = _get_user_kb_or_403(db, kb_id, current_user, required="owner")

    # 删除向量集合（K2：复用启动时装配的向量库，不新建连接）
    try:
        get_rag(request).vector_store.delete_collection(kb_id)
    except Exception:
        logger.exception("删除向量集合失败 kb_id=%s", kb_id)

    db.delete(kb)
    return {"status": "deleted"}


# ---- I2 RBAC：成员管理 ----

@router.get("/{kb_id}/members", response_model=list[MemberOut])
def list_members(
    kb_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[MemberOut]:
    """成员列表：owner（创建者）在前，其后为协作成员。editor 及以上可查看。"""
    kb = _get_user_kb_or_403(db, kb_id, current_user, required="editor")
    owner = db.query(User).filter(User.id == kb.user_id).first()
    out = [MemberOut(user_id=owner.id, username=owner.username, role="owner", created_at=kb.created_at)]
    rows = (
        db.query(KnowledgeBaseMember, User)
        .join(User, User.id == KnowledgeBaseMember.user_id)
        .filter(KnowledgeBaseMember.kb_id == kb_id)
        .order_by(KnowledgeBaseMember.created_at)
        .all()
    )
    out.extend(
        MemberOut(user_id=m.user_id, username=u.username, role=m.role, created_at=m.created_at)
        for m, u in rows
    )
    return out


@router.post(
    "/{kb_id}/members", response_model=MemberOut, status_code=status.HTTP_201_CREATED
)
def add_member(
    kb_id: int,
    req: MemberAddRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MemberOut:
    """按 username 添加协作成员（editor/viewer）。仅 owner。"""
    kb = _get_user_kb_or_403(db, kb_id, current_user, required="owner")
    target = db.query(User).filter(User.username == req.username).first()
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.id == kb.user_id:
        raise HTTPException(status_code=409, detail="该用户已是知识库 owner")
    if (
        db.query(KnowledgeBaseMember)
        .filter(KnowledgeBaseMember.kb_id == kb_id, KnowledgeBaseMember.user_id == target.id)
        .first()
    ):
        raise HTTPException(status_code=409, detail="该用户已是知识库成员")
    member = KnowledgeBaseMember(kb_id=kb_id, user_id=target.id, role=req.role)
    db.add(member)
    db.flush()
    return MemberOut(
        user_id=target.id, username=target.username, role=req.role, created_at=member.created_at
    )


@router.patch("/{kb_id}/members/{user_id}", response_model=MemberOut)
def update_member(
    kb_id: int,
    user_id: int,
    req: MemberRoleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MemberOut:
    """修改成员角色（editor ↔ viewer）。仅 owner；owner 行不可改。"""
    kb = _get_user_kb_or_403(db, kb_id, current_user, required="owner")
    if user_id == kb.user_id:
        raise HTTPException(status_code=400, detail="不能修改 owner 的角色")
    member = (
        db.query(KnowledgeBaseMember)
        .filter(KnowledgeBaseMember.kb_id == kb_id, KnowledgeBaseMember.user_id == user_id)
        .first()
    )
    if member is None:
        raise HTTPException(status_code=404, detail="成员不存在")
    user = db.query(User).filter(User.id == user_id).first()
    member.role = req.role
    db.flush()
    return MemberOut(
        user_id=user_id, username=user.username if user else f"user_{user_id}",
        role=req.role, created_at=member.created_at,
    )


@router.delete("/{kb_id}/members/{user_id}")
def remove_member(
    kb_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """移除协作成员。仅 owner；owner 行不可移。"""
    kb = _get_user_kb_or_403(db, kb_id, current_user, required="owner")
    if user_id == kb.user_id:
        raise HTTPException(status_code=400, detail="不能移除 owner")
    member = (
        db.query(KnowledgeBaseMember)
        .filter(KnowledgeBaseMember.kb_id == kb_id, KnowledgeBaseMember.user_id == user_id)
        .first()
    )
    if member is None:
        raise HTTPException(status_code=404, detail="成员不存在")
    db.delete(member)
    return {"status": "removed"}
