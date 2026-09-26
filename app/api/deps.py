"""FastAPI 依赖注入：数据库会话、当前用户。

所有需要鉴权的 API 积木（B3–B6）通过 `get_current_user` 获取登录用户。
"""
from __future__ import annotations

import logging
from collections.abc import Generator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.models import User

logger = logging.getLogger("app.api.deps")

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


def get_rag(request: Request):
    """取用启动时装配好的 RagPipeline 单例（K2）。

    单例在 lifespan 里构造。若未触发 lifespan 就取用（例如没走 ``with`` 的
    ``TestClient``），给出可读的 RuntimeError 而不是裸 AttributeError。
    """
    rag = getattr(request.app.state, "rag", None)
    if rag is None:
        raise RuntimeError(
            "app.state.rag 未初始化：RagPipeline 在 lifespan 里装配，请确认应用已启动"
            "（TestClient 需用 `with` 上下文触发 lifespan）。"
        )
    return rag


def get_ingestion(request: Request):
    """取用启动时装配好的 IngestionPipeline 单例（K2），与 RAG 共用向量库与 embedding。"""
    ingestion = getattr(request.app.state, "ingestion", None)
    if ingestion is None:
        raise RuntimeError(
            "app.state.ingestion 未初始化：IngestionPipeline 在 lifespan 里装配，"
            "请确认应用已启动（TestClient 需用 `with` 上下文触发 lifespan）。"
        )
    return ingestion


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """从 Authorization: Bearer <token> 解析 JWT，返回当前登录用户。

    令牌无效、过期、或用户不存在均返回 401。

    K8-4：三条 401 分支各自留下 warning。此前完全无日志，导致
    「用户 token 过期」和「服务端 JWT_SECRET 被改过」这两种现象
    **在日志上长得一模一样**（都是 401），只能靠猜。
    """
    token = credentials.credentials
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        username: str | None = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的令牌")
    except JWTError as exc:
        # 只记原因与路径，**不记 token 本身**（那是凭据）
        logger.warning("令牌校验失败 path=%s：%s", request.url.path, exc)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的令牌")

    user = db.query(User).filter(User.username == username).first()
    if user is None:
        logger.warning("令牌有效但用户不存在 path=%s username=%s", request.url.path, username)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    return user