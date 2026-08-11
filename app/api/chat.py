"""流式问答 + 溯源 API（B6）：非流式 / SSE 流式双模式，返回答案 + 引用来源。
G4：成功问答写 QaLog 日志，并提供历史查询接口。

所有接口需要登录。提问的知识库必须属于当前用户。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Generator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.kbs import _get_user_kb_or_403
from app.config import get_settings
from app.core.models import ChatMessage, Conversation, QaLog, User, utcnow
from app.storage.vector_store import build_vector_store

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

def _build_rag(request: Request):
    """复用 app.state 构建 RagPipeline。"""
    from app.providers.factory import build_embedding, build_llm, build_reranker
    from app.rag.pipeline import RagPipeline

    settings = get_settings()
    return RagPipeline(
        settings=settings,
        session_factory=request.app.state.session_factory,
        vector_store=build_vector_store(settings),
        embedding=build_embedding(settings),
        llm=build_llm(settings),
        reranker=build_reranker(settings),
    )


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


# ---- J2 会话持久化 helpers ----

def _parse_sources(raw: str) -> list[dict]:
    """反序列化消息 sources（防御非法 JSON）。"""
    try:
        data = json.loads(raw or "[]")
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


def _sources_json(result) -> str:
    """把 RagAnswer.sources 序列化为消息落库 JSON。"""
    return json.dumps(
        [
            {
                "filename": s.filename,
                "chunk_index": s.chunk_index,
                "content": s.content,
                "score": round(s.score, 4),
            }
            for s in result.sources
        ],
        ensure_ascii=False,
    )


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

    rag = _build_rag(request)
    try:
        result = rag.ask(kb_id, req.query, mode=req.mode, history=history)
    except Exception:
        raise HTTPException(status_code=502, detail="问答服务暂时不可用")

    _log_qa(db, current_user, kb_id, result)

    if conv is not None:
        _save_turn(db, conv, "user", req.query)
        _save_turn(db, conv, "assistant", result.answer,
                   sources=_sources_json(result), rewritten_query=result.rewritten_query)
        _touch_conversation(db, conv, req.query)

    return AskResponse(
        query=result.query,
        answer=result.answer,
        sources=_to_sources(result.sources),
        rewritten_query=result.rewritten_query,
        conversation_id=conv.id if conv else None,
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
    """向知识库提问，SSE 流式返回答案 + 最终来源。J2：支持会话落库（同 ask）。"""
    _get_user_kb_or_403(db, kb_id, current_user)

    conv = None
    history = req.history or None
    if req.conversation_id is not None:
        conv = _get_user_conversation(db, kb_id, req.conversation_id, current_user)
        history = _conversation_history(db, conv)

    rag = _build_rag(request)
    try:
        result = rag.ask(kb_id, req.query, mode=req.mode, history=history)
    except Exception:
        raise HTTPException(status_code=502, detail="问答服务暂时不可用")

    _log_qa(db, current_user, kb_id, result)

    if conv is not None:
        _save_turn(db, conv, "user", req.query)
        _save_turn(db, conv, "assistant", result.answer,
                   sources=_sources_json(result), rewritten_query=result.rewritten_query)
        _touch_conversation(db, conv, req.query)

    def event_stream() -> Generator[str, None, None]:
        """逐字符流式推送答案（V1：完整生成后分片模拟，效果等效）。"""
        # 逐 token 推送答案
        chunk_size = 3
        for i in range(0, len(result.answer), chunk_size):
            token = result.answer[i : i + chunk_size]
            yield f"data: {json.dumps({'type': 'token', 'content': token}, ensure_ascii=False)}\n\n"

        # 推送来源（含改写问句与会话 id，便于多轮调试）
        sources_data = [
            {
                "filename": s.filename,
                "chunk_index": s.chunk_index,
                "content": s.content[:200],
                "score": round(s.score, 4),
            }
            for s in result.sources
        ]
        yield f"data: {json.dumps({'type': 'sources', 'sources': sources_data, 'rewritten_query': result.rewritten_query, 'conversation_id': conv.id if conv else None}, ensure_ascii=False)}\n\n"

        # 结束
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
