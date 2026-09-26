"""流式问答 + 溯源 API（B6）：非流式 / SSE 流式双模式，返回答案 + 引用来源。
G4：成功问答写 QaLog 日志，并提供历史查询接口。
K1：/ask/stream 改为真流式（逐 token 下发，来源前置，落库后置）。

所有接口需要登录。提问的知识库必须属于当前用户。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from types import SimpleNamespace
from typing import Generator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_rag
from app.api.kbs import _get_user_kb_or_403
from app.core.models import ChatMessage, Conversation, QaLog, User, utcnow
from app.storage.db import get_db as open_db_session

logger = logging.getLogger("app.chat")

router = APIRouter(prefix="/api/kbs", tags=["chat"])


# ---- schemas ----

class ChatTurn(BaseModel):
    """J1 多轮：一轮对话历史（只保留文本，不带引用来源）。"""
    role: str
    content: str = Field(..., min_length=1, max_length=1000)

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in ("user", "assistant"):
            raise ValueError("role 必须是 user 或 assistant")
        return v


class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    mode: str | None = Field(
        default=None, description="检索模式：vector | hybrid；不传用服务端配置默认"
    )
    history: list[ChatTurn] = Field(
        default_factory=list,
        max_length=20,
        description="J1 多轮：最近对话轮次（最多 20 条），用于查询改写与上下文",
    )
    conversation_id: int | None = Field(
        default=None,
        description="J2 会话持久化：指定会话时，history 由服务端从库内推导并落库本轮消息",
    )

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in ("vector", "hybrid"):
            raise ValueError("mode 必须是 vector 或 hybrid")
        return v


class SourceRefOut(BaseModel):
    filename: str
    chunk_index: int
    content: str
    score: float


class AskResponse(BaseModel):
    query: str
    answer: str
    sources: list[SourceRefOut]
    rewritten_query: str = Field(
        default="", description="J1 多轮：改写后的检索问句（无历史/未改写时为原始 query）"
    )
    conversation_id: int | None = Field(
        default=None, description="J2：本轮消息归属的会话 id（未指定会话为 None）"
    )
    citation_issues: list[int] = Field(
        default_factory=list,
        description="K3 批 2：答案引用了不存在的来源编号（越界编号，升序去重）；空 = 引用全部有效",
    )


class ConversationOut(BaseModel):
    id: int
    kb_id: int
    title: str
    created_at: datetime
    updated_at: datetime


class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    sources: list[dict]
    rewritten_query: str
    created_at: datetime


class ConversationDetailOut(ConversationOut):
    messages: list[ChatMessageOut] = []


class ConversationCreateRequest(BaseModel):
    title: str = Field(default="新对话", min_length=1, max_length=200)


class ConversationRenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class QaLogOut(BaseModel):
    id: int
    query: str
    answer: str
    hit_doc_ids: list[int]
    hit_count: int
    created_at: datetime


# ---- helpers ----

def _to_sources(sources: list) -> list[SourceRefOut]:
    return [
        SourceRefOut(
            filename=s.filename,
            chunk_index=s.chunk_index,
            content=s.content,
            score=round(s.score, 4),
        )
        for s in sources
    ]


def _log_qa(db: Session, user: User, kb_id: int, result) -> None:
    """问答成功后写日志；日志失败只记日志，不影响问答主流程。"""
    try:
        doc_ids = sorted({s.document_id for s in result.sources})
        db.add(
            QaLog(
                user_id=user.id,
                kb_id=kb_id,
                query=result.query,
                answer=result.answer,
                hit_doc_ids=json.dumps(doc_ids, ensure_ascii=False),
                hit_count=len(doc_ids),
            )
        )
    except Exception:
        logger.exception("问答日志写入失败 kb_id=%s", kb_id)


def _parse_doc_ids(raw: str) -> list[int]:
    """反序列化 hit_doc_ids（防御非法 JSON）。"""
    try:
        data = json.loads(raw or "[]")
        return [int(x) for x in data] if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def _done_event(
    *,
    answer: str,
    rewritten_query: str,
    conversation_id: int | None,
    citation_issues: list[int],
) -> dict:
    """SSE ``done`` 事件的载荷（K3 批 2 加 citation_issues）。

    抽成纯函数是为了能离线断言：路由体在数据库不可用时跑不起来，
    而这个载荷的形状是前端契约的一部分，必须被测试钉住。
    """
    return {
        "type": "done",
        "answer": answer,
        "rewritten_query": rewritten_query,
        "conversation_id": conversation_id,
        "citation_issues": citation_issues,
    }


# ---- J2 会话持久化 helpers ----

def _parse_sources(raw: str) -> list[dict]:
    """反序列化消息 sources（防御非法 JSON）。"""
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def _sources_json(sources: list) -> str:
    """把 sources 列表序列化为消息落库 JSON（保留完整正文）。"""
    return json.dumps(
        [
            {
                "filename": s.filename,
                "chunk_index": s.chunk_index,
                "content": s.content,
                "score": round(s.score, 4),
            }
            for s in sources
        ],
        ensure_ascii=False,
    )


def _sse_sources(sources: list) -> list[dict]:
    """SSE 下发的来源（正文截断 200 字，减小单条事件体积）。"""
    return [
        {
            "filename": s.filename,
            "chunk_index": s.chunk_index,
            "content": s.content[:200],
            "score": round(s.score, 4),
        }
        for s in sources
    ]


def _get_user_conversation(db: Session, kb_id: int, conv_id: int, user: User) -> Conversation:
    """按 kb+用户 取会话：不存在或别库 404；他人私有会话 403。"""
    conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
    if conv is None or conv.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conv.user_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问该会话")
    return conv


def _conversation_history(db: Session, conv: Conversation) -> list[ChatMessage]:
    """会话内已有消息（id 正序），作为本轮改写与生成的上下文。"""
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv.id)
        .order_by(ChatMessage.id)
        .all()
    )


def _save_turn(
    db: Session,
    conv: Conversation,
    role: str,
    content: str,
    sources: str = "[]",
    rewritten_query: str = "",
) -> None:
    db.add(
        ChatMessage(
            conversation_id=conv.id,
            role=role,
            content=content,
            sources=sources,
            rewritten_query=rewritten_query,
        )
    )


def _touch_conversation(db: Session, conv: Conversation, query: str) -> None:
    """更新会话活动时间；首条消息时把标题设为提问前 30 字。"""
    conv.updated_at = utcnow()
    if conv.title == "新对话":
        conv.title = query[:30]


def _freeze_history(history: list | None) -> list[SimpleNamespace] | None:
    """把历史轮次物化成脱离 ORM 会话的轻量对象。

    K1：流式生成器在 Starlette 的线程池里执行，不能依赖请求期 Session 的实例状态，
    因此在进入生成器之前先把 ``role``/``content`` 取出来。
    """
    if not history:
        return None
    return [
        SimpleNamespace(role=getattr(t, "role", ""), content=getattr(t, "content", ""))
        for t in history
    ]


def _persist_stream_result(
    session_factory,
    *,
    user_id: int,
    kb_id: int,
    query: str,
    answer: str,
    sources: list,
    rewritten_query: str,
    conv_id: int | None,
) -> None:
    """K1 落库后置：答案完整（收到 done）后写 QaLog 与会话消息。

    - 另开一个数据库会话，与请求期 Session 解耦（生成器在线程池里跑）。
    - 失败只记日志，不影响已经下发给用户的答案。
    - 顺序与 ``ask()`` 一致：user 消息 → assistant 消息 → 更新会话活跃时间。
    """
    try:
        with open_db_session(session_factory) as db:
            doc_ids = sorted({s.document_id for s in sources})
            db.add(
                QaLog(
                    user_id=user_id,
                    kb_id=kb_id,
                    query=query,
                    answer=answer,
                    hit_doc_ids=json.dumps(doc_ids, ensure_ascii=False),
                    hit_count=len(doc_ids),
                )
            )

            if conv_id is not None:
                conv = db.query(Conversation).filter(Conversation.id == conv_id).first()
                if conv is not None:
                    _save_turn(db, conv, "user", query)
                    _save_turn(
                        db, conv, "assistant", answer,
                        sources=_sources_json(sources), rewritten_query=rewritten_query,
                    )
                    _touch_conversation(db, conv, query)
    except Exception:
        logger.exception("流式问答落库失败 kb_id=%s", kb_id)


# ---- B6-1: 非流式问答 ----


@router.post("/{kb_id}/ask", response_model=AskResponse)
def ask(
    kb_id: int,
    req: AskRequest,
    request: Request = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AskResponse:
    """向知识库提问，返回完整答案与引用来源。

    J1：可携带 history 走多轮改写；J2：带 conversation_id 时服务端从库内历史推导
    history，并把本轮 user/assistant 消息落库（成功后才落，失败不落半条）。
    """
    _get_user_kb_or_403(db, kb_id, current_user)

    conv = None
    history = req.history or None
    if req.conversation_id is not None:
        conv = _get_user_conversation(db, kb_id, req.conversation_id, current_user)
        history = _conversation_history(db, conv)

    rag = get_rag(request)
    try:
        result = rag.ask(kb_id, req.query, mode=req.mode, history=history)
    except Exception:
        # K8-3：这里原先只 raise、不打日志 —— 而流式路径（见 event_stream 的 except）
        # 有 logger.exception。同一条 RAG 链路两种待遇，非流式失败在服务端完全无痕，
        # 用户拿到的只有一句"问答服务暂时不可用"。补齐堆栈。
        logger.exception("非流式问答失败 kb_id=%s query=%s", kb_id, req.query[:50])
        raise HTTPException(status_code=502, detail="问答服务暂时不可用")

    _log_qa(db, current_user, kb_id, result)

    if conv is not None:
        _save_turn(db, conv, "user", req.query)
        _save_turn(db, conv, "assistant", result.answer,
                   sources=_sources_json(result.sources), rewritten_query=result.rewritten_query)
        _touch_conversation(db, conv, req.query)

    return AskResponse(
        query=result.query,
        answer=result.answer,
        sources=_to_sources(result.sources),
        rewritten_query=result.rewritten_query,
        conversation_id=conv.id if conv else None,
        citation_issues=result.citation_issues,
    )


# ---- B6-2: SSE 流式问答 ----


@router.post("/{kb_id}/ask/stream")
def ask_stream(
    kb_id: int,
    req: AskRequest,
    request: Request = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """向知识库提问，SSE **真流式**返回（K1）。

    事件序列：``stage(rewriting)`` → ``stage(retrieving)`` → ``stage(reranking)``
    → ``sources``（含 ``stage=generating``）→ ``token``* → ``done`` → ``data: [DONE]``。

    与 K1 之前的关键差别：
    - **不再先同步跑完整个 ask()**：第一字节不再等整段答案生成（真模型下 3–10 秒 → 亚秒级）。
    - **来源前置**：检索/重排完成即下发 sources，用户在答案打字过程中就能看到引用。
    - **错误事件化**：流一旦开始就无法再改 HTTP 状态码，生成期异常以 ``error`` 事件下发。
    - **落库后置**：QaLog 与会话消息在收到 ``done`` 之后才写，避免落一条空答案。
    """
    _get_user_kb_or_403(db, kb_id, current_user)

    conv = None
    history = req.history or None
    if req.conversation_id is not None:
        conv = _get_user_conversation(db, kb_id, req.conversation_id, current_user)
        history = _conversation_history(db, conv)

    # 进入生成器前把依赖请求期会话的东西取成纯数据
    history = _freeze_history(history)
    conv_id = conv.id if conv else None
    user_id = current_user.id
    session_factory = request.app.state.session_factory
    query = req.query
    mode = req.mode

    try:
        rag = get_rag(request)
    except Exception:
        logger.exception("构建 RAG 管线失败 kb_id=%s", kb_id)
        raise HTTPException(status_code=502, detail="问答服务暂时不可用")

    def sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def event_stream() -> Generator[str, None, None]:
        answer = ""
        sources: list = []
        rewritten_query = ""
        try:
            for evt in rag.ask_stream(kb_id, query, mode=mode, history=history):
                etype = evt.get("type")

                if etype == "stage":
                    # 心跳：每个阶段前发一行 SSE 注释，维持网关/浏览器连接活性
                    yield ": ping\n\n"
                    yield sse({"type": "stage", "stage": evt["stage"]})

                elif etype == "sources":
                    sources = evt.get("sources") or []
                    rewritten_query = evt.get("rewritten_query", "")
                    yield ": ping\n\n"
                    yield sse({
                        "type": "sources",
                        "sources": _sse_sources(sources),
                        "rewritten_query": rewritten_query,
                        "conversation_id": conv_id,
                        "stage": evt.get("stage", "generating"),
                    })

                elif etype == "token":
                    yield sse({"type": "token", "content": evt["content"]})

                elif etype == "done":
                    answer = evt.get("answer", "")
                    sources = evt.get("sources") or sources
                    rewritten_query = evt.get("rewritten_query", rewritten_query)
                    citation_issues = evt.get("citation_issues") or []
                    # ★ 落库后置：答案已完整，此时才写日志与会话消息
                    _persist_stream_result(
                        session_factory,
                        user_id=user_id,
                        kb_id=kb_id,
                        query=query,
                        answer=answer,
                        sources=sources,
                        rewritten_query=rewritten_query,
                        conv_id=conv_id,
                    )
                    yield sse(
                        _done_event(
                            answer=answer,
                            rewritten_query=rewritten_query,
                            conversation_id=conv_id,
                            citation_issues=citation_issues,
                        )
                    )

        except Exception:
            logger.exception("流式问答失败 kb_id=%s", kb_id)
            yield sse({"type": "error", "message": "问答服务暂时不可用"})

        # 兼容旧前端：保留结束标记
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---- J2: 会话 CRUD ----

@router.post(
    "/{kb_id}/conversations",
    response_model=ConversationOut,
    status_code=201,
)
def create_conversation(
    kb_id: int,
    req: ConversationCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationOut:
    """创建会话（默认标题"新对话"）。会话是当前用户私有数据，viewer+ 可建。"""
    _get_user_kb_or_403(db, kb_id, current_user)

    conv = Conversation(
        kb_id=kb_id,
        user_id=current_user.id,
        title=req.title.strip() or "新对话",
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return ConversationOut(
        id=conv.id,
        kb_id=conv.kb_id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )


@router.get("/{kb_id}/conversations", response_model=list[ConversationOut])
def list_conversations(
    kb_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ConversationOut]:
    """会话列表：仅当前用户的会话，按 updated_at 倒序（新对话置顶）。"""
    _get_user_kb_or_403(db, kb_id, current_user)

    convs = (
        db.query(Conversation)
        .filter(Conversation.kb_id == kb_id, Conversation.user_id == current_user.id)
        .order_by(Conversation.updated_at.desc())
        .all()
    )
    return [
        ConversationOut(
            id=c.id,
            kb_id=c.kb_id,
            title=c.title,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in convs
    ]


@router.get("/{kb_id}/conversations/{conv_id}", response_model=ConversationDetailOut)
def get_conversation(
    kb_id: int,
    conv_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationDetailOut:
    """会话详情：含按 id 正序的消息列表（assistant 消息带 sources/rewritten_query）。"""
    _get_user_kb_or_403(db, kb_id, current_user)
    conv = _get_user_conversation(db, kb_id, conv_id, current_user)

    messages = [
        ChatMessageOut(
            id=m.id,
            role=m.role,
            content=m.content,
            sources=_parse_sources(m.sources),
            rewritten_query=m.rewritten_query,
            created_at=m.created_at,
        )
        for m in _conversation_history(db, conv)
    ]
    return ConversationDetailOut(
        id=conv.id,
        kb_id=conv.kb_id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=messages,
    )


@router.patch("/{kb_id}/conversations/{conv_id}", response_model=ConversationOut)
def rename_conversation(
    kb_id: int,
    conv_id: int,
    req: ConversationRenameRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationOut:
    """会话改名（UI 暂不做，接口预留）。"""
    _get_user_kb_or_403(db, kb_id, current_user)
    conv = _get_user_conversation(db, kb_id, conv_id, current_user)

    conv.title = req.title.strip() or "新对话"
    db.commit()
    db.refresh(conv)
    return ConversationOut(
        id=conv.id,
        kb_id=conv.kb_id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )


@router.delete("/{kb_id}/conversations/{conv_id}")
def delete_conversation(
    kb_id: int,
    conv_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """删除会话（级联删除其消息）。"""
    _get_user_kb_or_403(db, kb_id, current_user)
    conv = _get_user_conversation(db, kb_id, conv_id, current_user)

    db.delete(conv)
    db.commit()
    return {"status": "ok"}


# ---- G4: 问答历史 ----

@router.get("/{kb_id}/qa-logs", response_model=list[QaLogOut])
def list_qa_logs(
    kb_id: int,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[QaLogOut]:
    """问答历史：按时间倒序返回最近 limit 条。"""
    _get_user_kb_or_403(db, kb_id, current_user)

    logs = (
        db.query(QaLog)
        .filter(QaLog.kb_id == kb_id)
        .order_by(QaLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        QaLogOut(
            id=log.id,
            query=log.query,
            answer=log.answer,
            hit_doc_ids=_parse_doc_ids(log.hit_doc_ids),
            hit_count=log.hit_count,
            created_at=log.created_at,
        )
        for log in logs
    ]
