"""文档上传、列表、状态查询、删除、重试（B4 + B5 + K4）。

所有接口需要登录。操作的知识库必须属于当前用户。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("app.documents")

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_ingestion, get_rag
from app.api.kbs import _get_user_kb_or_403
from app.core.models import Document, IngestionJob, User
from app.ingestion.parsers import SUPPORTED_EXTS

router = APIRouter(prefix="/api", tags=["documents"])

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


# ---- schemas ----

class JobStatus(BaseModel):
    """摄取任务进度（K4）：前端据此画进度条、显示失败原因与重试入口。"""

    model_config = ConfigDict(from_attributes=True)

    stage: str
    done_units: int = 0
    total_units: int = 0  # chunking 完成后才有值；为 0 时前端显示不定态
    error: str | None = None


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kb_id: int
    filename: str
    file_type: str
    status: str
    chunk_count: int
    created_at: datetime | None = None
    error: str | None = None
    job: JobStatus | None = None  # K4：同步摄取的文档没有 job（为 None）


# ---- helpers ----

def _job_of(db: Session, doc_id: int) -> IngestionJob | None:
    return db.query(IngestionJob).filter(IngestionJob.document_id == doc_id).first()


def _doc_out(doc: Document, job: IngestionJob | None) -> DocumentResponse:
    """组装响应。**不用 ORM 对象直接返回** —— Document 上没有 job 关系，
    pydantic 的 from_attributes 取不到，必须显式拼。"""
    return DocumentResponse(
        id=doc.id,
        kb_id=doc.kb_id,
        filename=doc.filename,
        file_type=doc.file_type,
        status=doc.status,
        chunk_count=doc.chunk_count,
        created_at=doc.created_at,
        error=doc.error,
        job=JobStatus.model_validate(job) if job is not None else None,
    )


# ---- B4: 上传 + 状态查询 ----

@router.post(
    "/kbs/{kb_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def upload_document(
    kb_id: int,
    file: UploadFile = File(...),
    request: Request = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DocumentResponse:
    """上传文档 → **立即返回 202**，解析/切片/向量化交给后台执行（K4）。

    改动前这里是**全程同步阻塞**：一份 100 页 PDF ≈ 400–600 切片 ≈ 25–40 次
    embedding 往返，接真 Key 时可达数十秒到数分钟，浏览器一直转圈、还极易撞上
    网关超时 —— 用户以为"上传失败"，其实后台还在跑。现在立刻拿到 ``id``，
    之后轮询 ``GET /api/documents/{id}`` 看 ``job.stage`` 与 ``done_units/total_units``。

    需要 editor 及以上角色。
    """
    # 1. 权限校验（I2 RBAC：上传文档 = 写操作，editor+）
    _get_user_kb_or_403(db, kb_id, current_user, required="editor")

    # 2. 文件校验（仍在请求期做完 —— 400 类错误要立刻告诉用户，
    #    不能丢进后台再变成一条 failed 记录）
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    ext = Path(file.filename).suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文档类型: {ext}（支持: {', '.join(sorted(SUPPORTED_EXTS))}）",
        )

    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"文件超过 {MAX_FILE_SIZE // 1024 // 1024} MB 限制")

    ingest = get_ingestion(request)

    # 3. 先落 Document(pending) + Job(pending) 拿到 doc_id，再按 doc_id 存原件 ——
    #    原件路径是**确定性推导**的（data/uploads/{id}{ext}），必须先有 id。
    doc, job = ingest.create_pending(kb_id, file.filename, ext.lstrip("."), current_user.id)
    try:
        ingest.save_upload(doc.id, ext, content)
    except Exception:
        # 落盘失败就别留一条永远 pending、也没法重试的记录
        ingest.delete_document(doc.id)
        raise

    # K6：上传即失效一次（保守）。job 落终态时**还会再失效一次** —— 异步下真正的
    # 语料变更发生在后台完成那一刻，见 IngestionPipeline.on_corpus_changed。
    _invalidate_cache(request, kb_id)

    # 4. 交给执行器，不等待
    ingest.enqueue(doc.id)
    return _doc_out(doc, job)


def _invalidate_cache(request: Request, kb_id: int) -> None:
    """K6：失效该库问答缓存。失效失败不影响文档操作本身（只留痕）。"""
    try:
        get_rag(request).cache.invalidate_kb(kb_id)
    except Exception:
        logger.warning("问答缓存失效失败（不影响文档操作）kb_id=%s", kb_id, exc_info=True)


@router.get("/documents/{doc_id}", response_model=DocumentResponse)
def get_document_status(
    doc_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DocumentResponse:
    """查询文档摄取状态（前端轮询的就是这个接口）。"""
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 验证用户拥有该文档所属的知识库
    _get_user_kb_or_403(db, doc.kb_id, current_user)

    return _doc_out(doc, _job_of(db, doc_id))


@router.post(
    "/documents/{doc_id}/retry",
    response_model=DocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_document(
    doc_id: int,
    request: Request = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DocumentResponse:
    """重新摄取一份 **failed** 的文档（K4）。需要 editor 及以上角色。

    非 failed → 409：对正在跑或已成功的文档重复入队会写出重复向量。
    """
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    _get_user_kb_or_403(db, doc.kb_id, current_user, required="editor")

    if doc.status != "failed":
        raise HTTPException(
            status_code=409,
            detail=f"只有 failed 状态的文档可以重试（当前 {doc.status}）",
        )

    ingest = get_ingestion(request)
    if not ingest.upload_path(doc.id, doc.file_type).exists():
        # 同步摄取（scripts/demo.py 等）建的文档没有留存原件
        raise HTTPException(
            status_code=409,
            detail="原始文件已不可用，无法重试，请删除后重新上传",
        )

    ingest.reset_job(doc_id)
    ingest.enqueue(doc_id)
    # reset_job 走的是另一个会话，本会话里的 doc / job 还是旧值 —— 全部作废重读
    db.expire_all()
    return _doc_out(doc, _job_of(db, doc_id))


# ---- B5: 列表 + 删除 ----


@router.get("/kbs/{kb_id}/documents", response_model=list[DocumentResponse])
def list_documents(
    kb_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DocumentResponse]:
    """列出知识库内所有文档（按创建时间倒序）。"""
    _get_user_kb_or_403(db, kb_id, current_user)
    docs = (
        db.query(Document)
        .filter(Document.kb_id == kb_id)
        .order_by(Document.created_at.desc())
        .all()
    )
    # 一次把该库的 job 全查出来配表，别按文档逐条查（N+1）
    jobs = {
        j.document_id: j
        for j in db.query(IngestionJob).filter(IngestionJob.kb_id == kb_id).all()
    }
    return [_doc_out(d, jobs.get(d.id)) for d in docs]


@router.delete("/documents/{doc_id}")
def delete_document(
    doc_id: int,
    request: Request = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str]:
    """删除文档（级联删除切片 + 向量数据）。需要 editor 及以上角色。"""
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    _get_user_kb_or_403(db, doc.kb_id, current_user, required="editor")

    # 清理向量 + 元数据 + 上传原件（pipeline 内部处理）
    try:
        ingest = get_ingestion(request)
        ingest.delete_document(doc_id)
    except Exception:
        logger.exception("删除文档向量/元数据失败 doc_id=%s", doc_id)

    # K6：同上 —— 文档删除后该库缓存全量失效
    _invalidate_cache(request, doc.kb_id)

    return {"status": "deleted"}
