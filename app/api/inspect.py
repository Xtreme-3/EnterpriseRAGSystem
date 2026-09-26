"""检索质检台 API（G1/H1）：输入 query 返回检索命中切片，不含 LLM 生成。

只跑 retriever，把 chat 接口黑盒掉的检索层暴露出来。
H1 起支持检索模式 mode（vector | hybrid），响应带实际使用的 mode。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_rag
from app.api.kbs import _get_user_kb_or_403
from app.config import get_settings
from app.core.models import Document, User

logger = logging.getLogger("app.inspect")
router = APIRouter(prefix="/api/kbs", tags=["inspect"])


# ---- schemas ----

class InspectRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=0, ge=0, le=50, description="0 = 使用默认值")
    mode: str | None = Field(
        default=None, description="检索模式：vector | hybrid；不传用服务端配置默认"
    )
    rerank: bool | None = Field(
        default=None, description="是否重排（I1）：null 用服务端 Settings.rerank"
    )

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in ("vector", "hybrid"):
            raise ValueError("mode 必须是 vector 或 hybrid")
        return v


class InspectHit(BaseModel):
    document_id: int
    filename: str
    chunk_index: int
    content: str
    score: float  # 最终分数：重排后 = 重排分；未重排 = 检索分
    pre_score: float  # 重排前检索分（未重排时与 score 相同），I1 前后对比


class InspectResponse(BaseModel):
    query: str
    kb_id: int
    top_k: int
    mode: str
    rerank: bool
    hits: list[InspectHit]


# ---- endpoint ----

@router.post("/{kb_id}/inspect", response_model=InspectResponse)
def inspect_retrieval(
    kb_id: int,
    req: InspectRequest,
    request: Request = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InspectResponse:
    """检索质检：只跑 retriever.retrieve()，返回 top-k 命中切片及分数，不经过 LLM 生成。"""
    _get_user_kb_or_403(db, kb_id, current_user)

    settings = get_settings()
    k = req.top_k if req.top_k > 0 else settings.top_k
    mode = req.mode or settings.retrieval_mode
    effective_rerank = req.rerank if req.rerank is not None else settings.rerank

    from app.providers.factory import build_reranker
    from app.rag.reranker import Reranker

    try:
        # K2：复用启动时装配的 retriever（内里是同一份 vector_store + embedding），
        # 不再每请求新建 PG engine 与 httpx 连接池
        hits = get_rag(request).retriever.retrieve(kb_id, req.query, k, mode=mode)
    except Exception:
        logger.exception("检索服务异常 kb_id=%s", kb_id)
        raise HTTPException(status_code=502, detail="检索服务暂时不可用")

    # I1 重排：先记录重排前检索分，重排后命中 score 替换为重排分（前后对比）
    pre_map = {(h.document_id, h.chunk_index): h.score for h in hits}
    if effective_rerank and hits:
        try:
            reranker = Reranker(build_reranker(settings.model_copy(update={"rerank": True})))
            hits = reranker.rerank(req.query, hits, top_n=k)
        except Exception:
            logger.exception("重排服务异常 kb_id=%s", kb_id)
            raise HTTPException(status_code=502, detail="重排服务暂时不可用")

    # 补充文档名
    doc_ids = {h.document_id for h in hits}
    filename_map: dict[int, str] = {}
    if doc_ids:
        docs = db.query(Document).filter(Document.id.in_(doc_ids)).all()
        filename_map = {d.id: d.filename for d in docs}

    return InspectResponse(
        query=req.query,
        kb_id=kb_id,
        top_k=k,
        mode=mode,
        rerank=effective_rerank,
        hits=[
            InspectHit(
                document_id=h.document_id,
                filename=filename_map.get(h.document_id, f"doc_{h.document_id}"),
                chunk_index=h.chunk_index,
                content=h.content,
                score=round(h.score, 4),
                pre_score=round(pre_map.get((h.document_id, h.chunk_index), h.score), 4),
            )
            for h in hits
        ],
    )
