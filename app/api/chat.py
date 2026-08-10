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
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.kbs import _get_user_kb_or_403
from app.config import get_settings
from app.core.models import QaLog, User
from app.storage.vector_store import build_vector_store

logger = logging.getLogger("app.chat")

router = APIRouter(prefix="/api/kbs", tags=["chat"])


# ---- schemas ----

class AskRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)


class SourceRefOut(BaseModel):
    filename: str
    chunk_index: int
    content: str
    score: float


class AskResponse(BaseModel):
    query: str
    answer: str
    sources: list[SourceRefOut]


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


# ---- B6-1: 非流式问答 ----


@router.post("/{kb_id}/ask", response_model=AskResponse)
def ask(
    kb_id: int,
    req: AskRequest,
    request: Request = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AskResponse:
    """向知识库提问，返回完整答案与引用来源。"""
    _get_user_kb_or_403(db, kb_id, current_user)

    rag = _build_rag(request)
    try:
        result = rag.ask(kb_id, req.query)
    except Exception:
        raise HTTPException(status_code=502, detail="问答服务暂时不可用")

    _log_qa(db, current_user, kb_id, result)

    return AskResponse(
        query=result.query,
        answer=result.answer,
        sources=_to_sources(result.sources),
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
    """向知识库提问，SSE 流式返回答案 + 最终来源。"""
    _get_user_kb_or_403(db, kb_id, current_user)

    rag = _build_rag(request)
    try:
        result = rag.ask(kb_id, req.query)
    except Exception:
        raise HTTPException(status_code=502, detail="问答服务暂时不可用")

    _log_qa(db, current_user, kb_id, result)

    def event_stream() -> Generator[str, None, None]:
        """逐字符流式推送答案（V1：完整生成后分片模拟，效果等效）。"""
        # 逐 token 推送答案
        chunk_size = 3
        for i in range(0, len(result.answer), chunk_size):
            token = result.answer[i : i + chunk_size]
            yield f"data: {json.dumps({'type': 'token', 'content': token}, ensure_ascii=False)}\n\n"

        # 推送来源
        sources_data = [
            {
                "filename": s.filename,
                "chunk_index": s.chunk_index,
                "content": s.content[:200],
                "score": round(s.score, 4),
            }
            for s in result.sources
        ]
        yield f"data: {json.dumps({'type': 'sources', 'sources': sources_data}, ensure_ascii=False)}\n\n"

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
