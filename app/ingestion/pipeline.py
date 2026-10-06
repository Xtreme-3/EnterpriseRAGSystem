"""摄取流水线：解析 → 切片 → embedding → 写元数据 + 向量库。

对上层暴露 create_kb / ingest_file / delete_document / delete_kb。
文档状态机：pending → processing → indexed | failed。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from app.config import Settings, get_settings
from app.core.models import Chunk, Document, IngestionJob, KnowledgeBase, utcnow
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
        executor: ThreadPoolExecutor | None = None,
        on_corpus_changed: Callable[[int], None] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        if session_factory is None:
            from app.storage.db import init_db

            _, self.session_factory = init_db(self.settings)
        else:
            self.session_factory = session_factory
        self.vector_store = vector_store or build_vector_store(self.settings)
        self.embedding = embedding or build_embedding(self.settings)
        # K4：后台摄取执行器。为 None 时 ``enqueue`` 退化为**同步执行** ——
        # 脚本（demo.py / reingest）与直接构造 pipeline 的单测不需要线程池，
        # 行为与改动前完全一致。HTTP 路径由 lifespan 注入 app.state 那个。
        self.executor = executor
        # K4：语料变更回调（kb_id）。**必须在 job 落终态时触发，而不是在上传时** ——
        # 异步下上传那一刻向量还没写进去，此时失效完，用户在"处理中"期间提问会
        # 缓存一个不含新文档的答案，且此后不再失效 → 旧答案被永久固化。
        # lifespan 把它接到 RagPipeline.cache.invalidate_kb（K6）。
        self.on_corpus_changed = on_corpus_changed

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

    # ---- K4：异步摄取 ----
    #
    # 与同步 ingest_file 的关系：同步版**原样保留**（demo 脚本、pipeline 单测在用），
    # 异步版是另一条入口，两者共用 _chunk / 向量库 / 失败落盘逻辑。

    @property
    def uploads_dir(self) -> Path:
        return Path(self.settings.data_dir) / "uploads"

    def upload_path(self, doc_id: int, ext: str) -> Path:
        """上传原件的落盘路径。

        路径由 ``doc_id + 扩展名`` **确定性推导**，不往 ``documents`` 加 ``path`` 列
        —— ``create_all`` 不会给已存在的表加列，SQLite 分支没有迁移路径（本项目
        踩过 ``no column named user_id``）。代价是文件必须严格按这个命名约定存放，
        由 ``save_upload`` 保证。
        """
        return self.uploads_dir / f"{doc_id}.{ext.lstrip('.')}"

    def save_upload(self, doc_id: int, ext: str, content: bytes) -> Path:
        """把上传原件写进 ``data/uploads``，供摄取**与重试**复用。

        改动前上传走 ``tempfile`` 且请求一结束就删 —— 摄取失败之后除了重传没有
        第二条路，而重试恰恰是异步摄取最需要的配套能力。
        """
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        p = self.upload_path(doc_id, ext)
        p.write_bytes(content)
        return p

    def create_pending(
        self, kb_id: int, filename: str, ext: str, user_id: int
    ) -> tuple[Document, IngestionJob]:
        """建 ``Document(pending)`` + ``IngestionJob(pending)``，立即返回（不阻塞）。"""
        with get_db(self.session_factory) as db:
            doc = Document(kb_id=kb_id, filename=filename, file_type=ext, status="pending")
            db.add(doc)
            db.flush()
            job = IngestionJob(
                document_id=doc.id, kb_id=kb_id, user_id=user_id, stage="pending"
            )
            db.add(job)
            db.flush()
            return doc, job

    def enqueue(self, doc_id: int) -> None:
        """把摄取交给执行器；未配置执行器时**同步执行**（脚本 / 直接构造的单测）。"""
        if self.executor is None:
            self._run_job(doc_id)
        else:
            self.executor.submit(self._run_job, doc_id)

    def reset_job(self, doc_id: int) -> None:
        """重试前把 job 与 Document 一起拨回 pending（复用同一行，不新建 attempt）。"""
        with get_db(self.session_factory) as db:
            job = self._job_of(db, doc_id)
            if job is not None:
                job.stage = "pending"
                job.done_units = 0
                job.total_units = 0
                job.error = None
                job.started_at = None
                job.finished_at = None
            doc = db.get(Document, doc_id)
            if doc is not None:
                doc.status = "pending"
                doc.error = None

    def fail_stale_jobs(self) -> int:
        """启动自愈：把进程退出时卡在非终态的 job 一律置 failed。

        执行器是**进程内**线程池，随进程消失 —— 重启后那些 job 永远不会再被推进，
        前端会一直转圈且**没有任何出口**。显式标成失败，用户至少能看到原因并重试。
        """
        from app.core.models import TERMINAL_STAGES

        with get_db(self.session_factory) as db:
            stale = (
                db.query(IngestionJob)
                .filter(IngestionJob.stage.notin_(TERMINAL_STAGES))
                .all()
            )
            reason = "服务重启中断，请重试"
            for job in stale:
                job.stage = "failed"
                job.error = reason
                job.finished_at = utcnow()
                doc = db.get(Document, job.document_id)
                if doc is not None and doc.status not in TERMINAL_STAGES:
                    doc.status = "failed"
                    doc.error = reason
            return len(stale)

    # ---- K4 内部实现 ----

    def _job_of(self, db, doc_id: int) -> IngestionJob | None:
        return db.query(IngestionJob).filter(IngestionJob.document_id == doc_id).first()

    def _touch_job(self, doc_id: int, **fields) -> None:
        """按需更新 job 字段。

        进度**按批量更新**（每批 embed 一次），不是每个切片写一次 —— SQLite 单写锁下
        高频写会和其他 job 抢锁。
        """
        with get_db(self.session_factory) as db:
            job = self._job_of(db, doc_id)
            if job is None:
                return
            for key, value in fields.items():
                setattr(job, key, value)

    def _run_job(self, doc_id: int) -> None:
        """后台摄取的真正重活：分阶段推进 job，并把 Document.status 一起落定。

        在**工作线程**里跑，不能复用请求期的 DB 会话 —— 每一段自己开 ``get_db``。
        """
        with get_db(self.session_factory) as db:
            doc = db.get(Document, doc_id)
            if doc is None:
                # 上传后立刻把文档删了 → 任务作废，不是错误
                logger.info("摄取任务跳过：文档 %s 已不存在", doc_id)
                return
            kb_id, ext = doc.kb_id, doc.file_type
            doc.status = "processing"
            job = self._job_of(db, doc_id)
            if job is not None:
                job.stage = "parsing"
                job.done_units = 0
                job.total_units = 0
                job.error = None
                job.started_at = utcnow()
                job.finished_at = None

        try:
            path = self.upload_path(doc_id, ext)
            if not path.exists():
                raise FileNotFoundError(f"原始文件不可用（{path.name}），无法摄取")

            # 1) 解析
            text = parse_file(path)

            # 2) 切片（structure+semantic 策略会在这里逐句调 embedding，
            #    这部分耗时计入 chunking 阶段 —— 不追求精确，追求"有反馈"）
            self._touch_job(doc_id, stage="chunking")
            chunks = clean_chunks(self._chunk(text), path.suffix)
            if not chunks:
                raise ValueError("文档解析后无有效内容")
            self._touch_job(doc_id, stage="embedding", total_units=len(chunks))

            # 3) 向量化
            vectors = self._embed_with_progress(doc_id, chunks)

            # 4) 写元数据 + 向量库
            self._touch_job(doc_id, stage="indexing")
            self._index(kb_id, doc_id, chunks, vectors)
        except Exception as exc:
            self._fail_job(kb_id, doc_id, exc)
            return

        self._touch_job(doc_id, stage="done", finished_at=utcnow())
        with get_db(self.session_factory) as db:
            doc = db.get(Document, doc_id)
            if doc is not None:
                doc.status = "indexed"
                doc.error = None
                doc.chunk_count = len(chunks)
        self._notify_corpus_changed(kb_id)

    def _notify_corpus_changed(self, kb_id: int) -> None:
        """通知"这个库的语料变了"（→ K6 问答缓存全量失效）。

        回调失败**不能**影响摄取结果：文档已经写好了，缓存旧一点是可容忍的降级，
        让整个任务报错才是灾难。
        """
        if self.on_corpus_changed is None:
            return
        try:
            self.on_corpus_changed(kb_id)
        except Exception:
            logger.warning("语料变更回调失败（不影响摄取）kb_id=%s", kb_id, exc_info=True)

    def _embed_with_progress(self, doc_id: int, chunks: list[str]) -> list[list[float]]:
        """按 provider 的单批上限分批 embed，每批完成后累加 ``done_units``。

        ⚠️ 分批**不缩短总时长**（HTTP 往返次数由 provider 内部决定，这里只是把它
        显式化以便上报进度），它的价值是让前端看得见 done/total 在动。
        异步化的收益是 **UI 不阻塞**，与"总时长变短"是两件事，别混在一个数字里归因。
        """
        batch = max(1, int(getattr(self.embedding, "max_batch", 16)))
        vectors: list[list[float]] = []
        for i in range(0, len(chunks), batch):
            vectors.extend(self.embedding.embed(chunks[i : i + batch]))
            self._touch_job(doc_id, done_units=len(vectors))
        return vectors

    def _index(self, kb_id: int, doc_id: int, chunks: list[str], vectors: list[list[float]]) -> None:
        """写切片元数据与向量。

        **先清残留**：重试时上一次可能已经写了一半，不清就会留下重复向量
        （检索里出现同一段内容两份）。

        ⚠️ **`ensure_collection` 必须排在 `delete_document` 前面**：pgvector 模式下
        向量表 ``vectors`` 是 ensure_collection → _ensure_ext 懒建的，反过来写会在
        全新库上先撞 ``relation "vectors" does not exist``（这条是靠真跑 pgvector
        模式的测试照出来的 —— chroma 的 delete 会顺手建集合，所以本地 chroma 下看不出来）。
        """
        self.vector_store.ensure_collection(kb_id, self.embedding.dim)
        self.vector_store.delete_document(kb_id, doc_id)
        with get_db(self.session_factory) as db:
            db.query(Chunk).filter(Chunk.document_id == doc_id).delete()
            for i, content in enumerate(chunks):
                db.add(Chunk(document_id=doc_id, kb_id=kb_id, chunk_index=i, content=content))

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

    def _fail_job(self, kb_id: int, doc_id: int, exc: Exception) -> None:
        logger.error("异步摄取失败 kb_id=%s doc_id=%s：%s", kb_id, doc_id, exc)
        # **清掉可能已写了一半的向量与切片**：失败文档不该在检索结果里露头，
        # 否则用户会看到一份"上传失败但还能被引用"的文档；同时 G3 的
        # 「向量数 == chunk_count」一致性检查也会因为半截数据漂移。
        # 尽力而为 —— 清理本身失败不能掩盖真正的失败原因。
        try:
            self.vector_store.delete_document(kb_id, doc_id)
            with get_db(self.session_factory) as db:
                db.query(Chunk).filter(Chunk.document_id == doc_id).delete()
        except Exception:
            logger.warning("清理失败摄取的残留失败 doc_id=%s", doc_id, exc_info=True)

        self._touch_job(doc_id, stage="failed", error=str(exc), finished_at=utcnow())
        with get_db(self.session_factory) as db:
            doc = db.get(Document, doc_id)
            if doc is not None:
                doc.status = "failed"
                doc.error = str(exc)
                doc.chunk_count = 0
        self._notify_corpus_changed(kb_id)

    def delete_document(self, document_id: int) -> None:
        with get_db(self.session_factory) as db:
            doc = db.get(Document, document_id)
            if doc is None:
                return
            kb_id = doc.kb_id
            ext = doc.file_type
        self.vector_store.delete_document(kb_id, document_id)
        with get_db(self.session_factory) as db:
            doc = db.get(Document, document_id)
            if doc is not None:
                db.delete(doc)
        # K4：上传原件也要删，否则 data/uploads 只增不减
        try:
            self.upload_path(document_id, ext).unlink(missing_ok=True)
        except OSError:
            logger.warning("删除上传原件失败 doc_id=%s", document_id, exc_info=True)

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
