"""鉴权路由：注册、登录、当前用户、登出（B2）。"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import jwt
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.config import get_settings
from app.core import audit
from app.core.models import User

logger = logging.getLogger("app.api.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ---- 密码哈希 ----

def _hash_password(password: str) -> str:
    """bcrypt 哈希，自动截断到 72 字节（bcrypt 上限）。"""
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode("utf-8"))


# ---- Pydantic schemas ----

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=6, max_length=72)  # bcrypt 上限 72 字节


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str  # I2 RBAC：user | admin
    created_at: datetime


# ---- helpers ----

def _create_access_token(username: str) -> str:
    settings = get_settings()
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    return jwt.encode(
        {"sub": username, "exp": expire},
        settings.jwt_secret,
        algorithm="HS256",
    )


# ---- endpoints ----

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, db: Session = Depends(get_db)) -> User:
    if db.query(User).filter(User.username == req.username).first():
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = User(username=req.username, hashed_password=_hash_password(req.password))
    db.add(user)
    db.flush()
    # 成功路径：审计与业务同事务（请求会话）
    audit.record_audit(db, audit.ACTIONS.REGISTER, actor=user,
                       target=audit.user_target(user.id, user.username))
    return user


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not _verify_password(req.password, user.hashed_password):
        # K8-4：登录失败是安全事件。没有日志就没有暴力破解 / 撞库的审计线索，
        # 线上"用户说密码明明是对的"这类报障也完全无法复盘。
        # 只记用户名，**绝不记密码**；连"用户是否存在"一起区分，便于判断是撞库还是打错。
        reason = "用户不存在" if user is None else "密码错误"
        logger.warning("登录失败 username=%s reason=%s", req.username, reason)
        # L2 失败路径：必须独立短会话（下面的 raise 触发请求会话 rollback，挂上去必丢）。
        # 撞库探测时用户不存在 → actor_id=NULL，actor_name 恒记尝试的用户名
        audit.record_audit_detached(request, audit.ACTIONS.LOGIN_FAILED, actor=user,
                                    actor_name=req.username, detail=f"reason={reason}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    audit.record_audit(db, audit.ACTIONS.LOGIN, actor=user)
    return TokenResponse(access_token=_create_access_token(user.username))


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post("/logout")
def logout() -> dict[str, str]:
    return {"status": "ok"}