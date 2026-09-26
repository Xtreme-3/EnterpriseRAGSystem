"""文档上传、列表、状态查询、删除（B4 + B5）。

所有接口需要登录。操作的知识库必须属于当前用户。
"""
from __future__ import annotations

import logging
import tempfile
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("app.documents")

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_ingestion
from app.api.kbs import _get_user_kb_or_403
from app.core.models import Document, User
from app.ingestion.parsers import SUPPORTED_EXTS

router = APIRouter(prefix="/api", tags=["documents"])

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB


# ---- schemas ----

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


# ---- B4: 上传 + 状态查询 ----

@router.post(
    "/kbs/{kb_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_document(
    kb_id: int,
    file: UploadFile = File(...),
    request: Request = None,  # type: ignore[assignment]
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Document:
    """上传文档到知识库，同步完成向量化。

    支持 PDF / DOCX / MD / TXT，最大 20 MB。需要 editor 及以上角色。
    """
    # 1. 权限校验（I2 RBAC：上传文档 = 写操作，editor+）
    _get_user_kb_or_403(db, kb_id, current_user, required="editor")

    # 2. 文件校验
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    ext = Path(file.filename).suffix.lower()
    if ext not in SUPPORTED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文档类型: {ext}（支持: {', '.join(sorted(SUPPORTED_EXTS))}）",
        )

    # 3. 读取并暂存文件
    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"文件超过 {MAX_FILE_SIZE // 1024 // 1024} MB 限制")

    # 写临时文件（ingest_file 需要文件路径）
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    # 4. 摄取
    try:
        ingest = get_ingestion(request)
        doc = ingest.ingest_file(kb_id, tmp_path, display_name=file.filename)
    except Exception:
        # 失败时也尝试清理临时文件
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass
        raise
    finally:
        # 成功时清理临时文件
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass

    return doc


@router.get("/documents/{doc_id}", response_model=DocumentResponse)
def get_document_status(
    doc_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Document:
    """查询文档摄取状态（必须拥有该文档所属的知识库）。"""
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 验证用户拥有该文档所属的知识库
    _get_user_kb_or_403(db, doc.kb_id, current_user)

    return doc


# ---- B5: 列表 + 删除 ----


@router.get("/kbs/{kb_id}/documents", response_model=list[DocumentResponse])
def list_documents(
    kb_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Document]:
    """列出知识库内所有文档（按创建时间倒序）。"""
    _get_user_kb_or_403(db, kb_id, current_user)
    return (
        db.query(Document)
        .filter(Document.kb_id == kb_id)
        .order_by(Document.created_at.desc())
        .all()
    )


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

    # 清理向量 + 元数据（pipeline 内部处理）
    try:
        ingest = get_ingestion(request)
        ingest.delete_document(doc_id)
    except Exception:
        logger.exception("删除文档向量/元数据失败 doc_id=%s", doc_id)

    return {"status": "deleted"}
