"""摄取诊断 API（G2）：知识库体检报告。

纯元数据查询（Document.status / error / chunk_count），不动向量库。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.kbs import _get_user_kb_or_403
from app.core.models import Document, User

router = APIRouter(prefix="/api/kbs", tags=["diagnostics"])


# ---- schemas ----

class DiagnosticDoc(BaseModel):
    """失败组中涉及的文档（轻量）。"""

    id: int
    filename: str


class FailureGroup(BaseModel):
    """按 error 文本聚合的一组失败文档。"""

    error: str
    count: int
    documents: list[DiagnosticDoc]


class AnomalyDoc(BaseModel):
    """异常清单中的一条（failed 或零切片文档）。"""

    id: int
    filename: str
    status: str
    chunk_count: int
    error: str | None = None


class Summary(BaseModel):
    total_documents: int
    indexed: int
    failed: int
    processing: int
    pending: int
    total_chunks: int


class ConsistencyIssue(BaseModel):
    """元数据 chunk_count 与向量库实际向量数不一致的一条。"""

    kind: str  # missing_index / count_drift / orphan_vector
    document_id: int
    filename: str | None
    meta_count: int
    vector_count: int


class Consistency(BaseModel):
    checked: bool  # False = 向量库不可达，未能对比
    issues: list[ConsistencyIssue]


class DiagnosticsResponse(BaseModel):
    kb_id: int
    summary: Summary
    failures: list[FailureGroup]
    anomalies: list[AnomalyDoc]
    consistency: Consistency


# ---- helpers ----

def _check_consistency(kb_id: int, docs: list[Document]) -> Consistency:
    """对比元数据 chunk_count 与向量库实际向量数，报告缺失/漂移/孤儿。"""
    from app.config import get_settings
    from app.storage.vector_store import build_vector_store

    meta_counts = {d.id: (d.chunk_count or 0) for d in docs}
    filenames = {d.id: d.filename for d in docs}

    try:
        store = build_vector_store(get_settings())
        vector_counts = store.document_counts(kb_id)
    except Exception:
        # 向量库不可达时只置 checked=False，不拖垮体检接口
        return Consistency(checked=False, issues=[])

    issues: list[ConsistencyIssue] = []
    for doc_id, meta_count in meta_counts.items():
        vec_count = vector_counts.get(doc_id, 0)
        if meta_count == vec_count:
            continue
        if meta_count > 0 and vec_count == 0:
            kind = "missing_index"  # 元数据说有条目，向量库没有
        elif meta_count == 0 and vec_count > 0:
            kind = "orphan_vector"  # 文档存在但无 chunk，向量库却有残留
        else:
            kind = "count_drift"  # 两侧都有但数量不等
        issues.append(
            ConsistencyIssue(
                kind=kind,
                document_id=doc_id,
                filename=filenames.get(doc_id),
                meta_count=meta_count,
                vector_count=vec_count,
            )
        )

    # 完全不存在于元数据中的孤儿向量（手工写入等）
    for doc_id, vec_count in vector_counts.items():
        if doc_id not in meta_counts and vec_count > 0:
            issues.append(
                ConsistencyIssue(
                    kind="orphan_vector",
                    document_id=doc_id,
                    filename=None,
                    meta_count=0,
                    vector_count=vec_count,
                )
            )

    return Consistency(checked=True, issues=issues)


# ---- endpoint ----

@router.get("/{kb_id}/diagnostics", response_model=DiagnosticsResponse)
def get_kb_diagnostics(
    kb_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DiagnosticsResponse:
    """知识库体检：文档健康度总览 + 失败原因聚合 + 异常文档清单。"""
    _get_user_kb_or_403(db, kb_id, current_user)

    docs = db.query(Document).filter(Document.kb_id == kb_id).all()

    # 状态计数 + 总切片数
    indexed = failed = processing = pending = 0
    total_chunks = 0
    for d in docs:
        total_chunks += d.chunk_count or 0
        if d.status == "indexed":
            indexed += 1
        elif d.status == "failed":
            failed += 1
        elif d.status == "processing":
            processing += 1
        else:
            pending += 1

    # 失败原因聚合（按 error 文本分组）
    failure_groups: dict[str, list[DiagnosticDoc]] = {}
    for d in docs:
        if d.status == "failed":
            key = d.error or "未知错误"
            failure_groups.setdefault(key, []).append(
                DiagnosticDoc(id=d.id, filename=d.filename)
            )
    failures = [
        FailureGroup(error=err, count=len(doc_list), documents=doc_list)
        for err, doc_list in failure_groups.items()
    ]

    # 异常清单：failed 文档 + 已处理但零切片的文档
    anomalies = [
        AnomalyDoc(
            id=d.id,
            filename=d.filename,
            status=d.status,
            chunk_count=d.chunk_count or 0,
            error=d.error,
        )
        for d in docs
        if d.status == "failed" or (d.status != "pending" and (d.chunk_count or 0) == 0)
    ]

    return DiagnosticsResponse(
        kb_id=kb_id,
        summary=Summary(
            total_documents=len(docs),
            indexed=indexed,
            failed=failed,
            processing=processing,
            pending=pending,
            total_chunks=total_chunks,
        ),
        failures=failures,
        anomalies=anomalies,
        consistency=_check_consistency(kb_id, docs),
    )
