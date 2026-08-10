"""FastAPI 依赖注入：数据库会话、当前用户。

所有需要鉴权的 API 积木（B3–B6）通过 `get_current_user` 获取登录用户。
"""
from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.models import User

security = HTTPBearer()


def get_db(request: Request) -> Generator[Session, None, None]:
    """每个请求创建一个独立数据库会话，请求结束时自动提交/回滚/关闭。"""
    session_factory = request.app.state.session_factory
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """从 Authorization: Bearer <token> 解析 JWT，返回当前登录用户。

    令牌无效、过期、或用户不存在均返回 401。
    """
    token = credentials.credentials
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        username: str | None = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的令牌")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的令牌")

    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return user