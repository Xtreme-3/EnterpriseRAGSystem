"""审计记账（L2）：一次写操作一条 append-only 记录。

**两个入口，按路径选**（2026-10-25 实测修正：最初设计「全程独立短会话」在 SQLite
下必撞 `database is locked` —— 成功路径上请求会话已经 flush 持有写锁，第二个会话
写不进去；且失败被 best-effort 吞掉 = 审计静默丢失。教训同 L1：设计先过实测）：

- ``record_audit(db, ...)`` —— **成功路径**。写入请求会话，与业务**同事务**提交：
  操作成功 ⇔ 留审计；业务回滚审计同亡（操作没成不算事实）。
- ``record_audit_detached(request, ...)`` —— **失败路径**（目前仅 401 登录失败）。
  独立短会话立即提交，因为路由随后 raise 会触发 ``get_db`` 的 rollback，
  挂在请求会话上的失败审计会跟着消失。此路径请求会话只做过 SELECT（无写锁），
  短会话写入无冲突。best-effort：审计失败只记 exception 日志，绝不影响业务操作。

**为什么不设 FK**：见 models.AuditLog 的类注释——删库不能带走它自己的删除记录。

action 清单与前端 Tag 语义色的映射见需求卡 ``docs/requirements/L2-audit-log.md``。
"""
from __future__ import annotations

import logging

from fastapi import Request
from sqlalchemy.orm import Session

from app.core.models import AuditLog, User

logger = logging.getLogger("app.core.audit")


class ACTIONS:
    """action 常量。新增时同步改前端 ``AuditLogs.vue`` 的中文映射表（漏改只降级显示原文）。"""

    LOGIN = "auth.login"
    LOGIN_FAILED = "auth.login_failed"
    REGISTER = "auth.register"
    KB_CREATE = "kb.create"
    KB_DELETE = "kb.delete"
    DOCUMENT_UPLOAD = "document.upload"
    DOCUMENT_DELETE = "document.delete"
    DOCUMENT_RETRY = "document.retry"
    MEMBER_ADD = "member.add"
    MEMBER_ROLE_CHANGE = "member.role_change"
    MEMBER_REMOVE = "member.remove"


def kb_target(kb_id: int, name: str) -> str:
    return f"kb:{kb_id}({name})"


def user_target(user_id: int, username: str) -> str:
    return f"user:{user_id}({username})"


def doc_target(doc_id: int, filename: str) -> str:
    return f"document:{doc_id}({filename})"


def _build_log(
    action: str,
    *,
    actor: User | None = None,
    actor_name: str | None = None,
    kb_id: int | None = None,
    target: str = "",
    detail: str = "",
) -> AuditLog:
    name = actor_name if actor_name is not None else (actor.username if actor else "")
    return AuditLog(
        action=action,
        actor_id=actor.id if actor else None,
        actor_name=name,
        kb_id=kb_id,
        target=target,
        detail=detail,
    )


def record_audit(
    db: Session,
    action: str,
    *,
    actor: User | None = None,
    actor_name: str | None = None,
    kb_id: int | None = None,
    target: str = "",
    detail: str = "",
) -> None:
    """成功路径：写入请求会话，与业务同事务提交（get_db 在请求结束时 commit）。

    ``actor_name`` 覆盖 ``actor.username`` 的情形一般只出现在失败路径；留在此处
    纯为让两个入口共用一套参数形状。
    """
    db.add(_build_log(action, actor=actor, actor_name=actor_name, kb_id=kb_id,
                      target=target, detail=detail))


def record_audit_detached(
    request: Request,
    action: str,
    *,
    actor: User | None = None,
    actor_name: str | None = None,
    kb_id: int | None = None,
    target: str = "",
    detail: str = "",
) -> None:
    """失败路径：独立短会话立即提交，best-effort（审计挂了不能让登录响应跟着挂）。"""
    session = request.app.state.session_factory()
    try:
        # 构造也要进 try：字段写错这类编程错误同样不能改变业务响应
        log = _build_log(action, actor=actor, actor_name=actor_name, kb_id=kb_id,
                         target=target, detail=detail)
        session.add(log)
        session.commit()
    except Exception:
        # 只留痕不外抛：审计存储挂了也不能改变业务响应（401 还是 401）
        logger.exception("审计记账失败 action=%s target=%s（不影响业务操作）", action, target)
        session.rollback()
    finally:
        session.close()
