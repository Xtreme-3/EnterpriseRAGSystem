"""摄取流水线：解析 → 切片 → embedding → 写元数据 + 向量库。

对上层暴露 create_kb / ingest_file / delete_document / delete_kb。
文档状态机：pending → processing → indexed | failed。
"""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.core.models import Chunk, Document, KnowledgeBase
from app.ingestion.chunker import split_semantic, split_structure, split_text
from app.ingestion.parsers import clean_chunks, parse_file
from app.providers.base import EmbeddingProvider
from app.storage.db import get_db
from app.storage.vector_store import ChunkToIndex, VectorStore, build_vector_store

logger = logging.getLogger(__name__)


class IngestionPipeline:
    def __init__(
        self,
        settings: Settings | None = None,
        session_factory: sessionmaker | None = None,
        vector_store: VectorStore | None = None,
        embedding: EmbeddingProvider | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        if session_factory is None:
            from app.storage.db import init_db

            _, self.session_factory = init_db(self.settings)
        else:
            self.session_factory = session_factory
        self.vector_store = vector_store or build_vector_store(self.settings)
        self.embedding = embedding or build_embedding(self.settings)

    # ---- 知识库 ----

    def create_kb(self, name: str, description: str = "", user_id: int = 0) -> KnowledgeBase:
        with get_db(self.session_factory) as db:
            kb = KnowledgeBase(name=name, description=description, user_id=user_id)
            db.add(kb)
            db.flush()
            kb_id = kb.id
        self.vector_store.ensure_collection(kb_id, self.embedding.dim)
        return kb

    def get_or_create_kb(self, name: str, description: str = "", user_id: int = 0) -> KnowledgeBase:
        """按名称取已有知识库，不存在则创建（幂等）。"""
        with get_db(self.session_factory) as db:
            kb = db.query(KnowledgeBase).filter(KnowledgeBase.name == name).first()
            if kb is not None:
                return kb
        return self.create_kb(name, description, user_id=user_id)

    def delete_kb(self, kb_id: int) -> None:
        self.vector_store.delete_collection(kb_id)
        with get_db(self.session_factory) as db:
            kb = db.get(KnowledgeBase, kb_id)
            if kb is not None:
                db.delete(kb)

    # ---- 文档 ----

    def ingest_file(self, kb_id: int, path: str | Path, display_name: str | None = None) -> Document:
        """同步摄取单个文件：解析、切片、向量化、入库。

        display_name: 展示文件名（用于临时文件场景，保留原始文件名）。
        """
        p = Path(path)
        ext = p.suffix.lower().lstrip(".")
        filename = display_name or p.name

        # 1. 解析 + 切片 + 向量化
        try:
            text = parse_file(p)
            # 切片后清理：markdown 的标题 `#` 必须留到这一步才剥 ——
            # 切分器靠它认章节边界，解析阶段就剥会把相邻章节并成一块。
            chunks = clean_chunks(self._chunk(text), p.suffix)
            if not chunks:
                raise ValueError("文档解析后无有效内容")
            vectors = self.embedding.embed(chunks)
        except Exception as exc:
            return self._fail(kb_id, filename, ext, exc)

        # 2. 写元数据（先 processing，向量写成功后置 indexed）
        with get_db(self.session_factory) as db:
            doc = Document(kb_id=kb_id, filename=filename, file_type=ext, status="processing")
            db.add(doc)
            db.flush()
            for i, content in enumerate(chunks):
                db.add(Chunk(document_id=doc.id, kb_id=kb_id, chunk_index=i, content=content))
            doc_id = doc.id

        # 3. 写向量库
        try:
            self.vector_store.ensure_collection(kb_id, self.embedding.dim)
            self.vector_store.add(
                kb_id,
                [
                    ChunkToIndex(
                        id=f"{doc_id}:{i}",
                        content=chunks[i],
                        vector=vectors[i],
                        document_id=doc_id,
                        kb_id=kb_id,
                        chunk_index=i,
                    )
                    for i in range(len(chunks))
                ],
            )
        except Exception as exc:
            return self._fail(kb_id, filename, ext, exc, doc_id=doc_id)

        # 4. 标记完成
        # K8-7：这一段原先没有 try/except —— 此处一旦抛错（DB 连接断、行被并发删掉），
        # 异常直接冒泡出 ingest_file，而文档已经以 processing 落库且**永远不会再变**：
        # 用户看到的是转圈转到天荒地老的"处理中"，没有失败原因、也没有重试入口。
        # 与 step1/step3 保持一致：失败一律走 _fail() 落成 failed。
        try:
            with get_db(self.session_factory) as db:
                doc = db.get(Document, doc_id)
                doc.status = "indexed"
                doc.chunk_count = len(chunks)
        except Exception as exc:
            return self._fail(kb_id, filename, ext, exc, doc_id=doc_id)
        return doc

    def delete_document(self, document_id: int) -> None:
        with get_db(self.session_factory) as db:
            doc = db.get(Document, document_id)
            if doc is None:
                return
            kb_id = doc.kb_id
        self.vector_store.delete_document(kb_id, document_id)
        with get_db(self.session_factory) as db:
            doc = db.get(Document, document_id)
            if doc is not None:
                db.delete(doc)

    def _chunk(self, text: str) -> list[str]:
        """按 Settings.chunk_strategy 选择切分策略（H2 语义切分）。"""
        size = self.settings.chunk_size
        overlap = self.settings.chunk_overlap
        strategy = self.settings.chunk_strategy
        if strategy == "structure":
            return split_structure(text, chunk_size=size, chunk_overlap=overlap)
        if strategy == "structure+semantic":
            return split_semantic(
                text,
                self.embedding.embed,
                threshold=self.settings.semantic_break_threshold,
                chunk_size=size,
                chunk_overlap=overlap,
            )
        return split_text(text, chunk_size=size, chunk_overlap=overlap)

    def _fail(self, kb_id: int, filename: str, ext: str, exc: Exception, doc_id: int | None = None) -> Document:
        """摄取失败：若已有 processing 记录则置为 failed，否则新建 failed 记录。

        K8-5：原先只写 DB 的 ``doc.error``、不打日志 —— 服务端日志里摄取失败**完全无痕**，
        只有打开 UI 才看得到 failed。批量导入出问题时，日志是唯一能回答
        "哪一份、为什么"的地方。
        """
        logger.error(
            "摄取失败 kb_id=%s file=%s type=%s doc_id=%s：%s",
            kb_id,
            filename,
            ext,
            doc_id,
            exc,
        )
        with get_db(self.session_factory) as db:
            if doc_id is not None:
                doc = db.get(Document, doc_id)
                if doc is not None:
                    doc.status = "failed"
                    doc.error = str(exc)
                    return doc
            doc = Document(
                kb_id=kb_id,
                filename=filename,
                file_type=ext,
                status="failed",
                error=str(exc),
            )
            db.add(doc)
            db.flush()
            return doc


def build_embedding(settings: Settings | None = None) -> EmbeddingProvider:
    from app.providers.factory import build_embedding as _build

    return _build(settings)
