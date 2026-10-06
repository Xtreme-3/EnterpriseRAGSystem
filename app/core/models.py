"""SQLAlchemy 元数据模型：知识库 / 文档 / 切片。

向量本身存在向量库（ChromaDB / pgvector），这里只存结构化的元数据与正文（供引用展示与再向量化）。
兼容 SQLite 与 PostgreSQL。
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship()
    documents: Mapped[list["Document"]] = relationship(
        back_populates="knowledge_base", cascade="all, delete-orphan"
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    kb_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(20))  # pdf / docx / md / txt
    status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # pending / processing / indexed / failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    knowledge_base: Mapped[KnowledgeBase] = relationship(back_populates="documents")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    kb_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)

    document: Mapped[Document] = relationship(back_populates="chunks")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    # 全局角色（I2 RBAC）：user | admin（admin 视为任意知识库的 owner）
    role: Mapped[str] = mapped_column(String(20), default="user", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeBaseMember(Base):
    """知识库协作成员与角色（I2 RBAC）。

    owner 不入本表（owner = KnowledgeBase.user_id，权威来源）；本表只存
    editor / viewer 协作成员。唯一约束 (kb_id, user_id) 防重复。
    """

    __tablename__ = "knowledge_base_members"
    __table_args__ = (
        UniqueConstraint("kb_id", "user_id", name="uq_kb_member"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kb_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20), default="viewer")  # editor | viewer
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# 摄取阶段（K4）。顺序即推进顺序；``TERMINAL_STAGES`` 里的两个是终态。
INGEST_STAGES = ("pending", "parsing", "chunking", "embedding", "indexing", "done", "failed")
TERMINAL_STAGES = ("done", "failed")


class IngestionJob(Base):
    """摄取任务（K4）：一次上传的进度与阶段，一份文档恒定一行。

    **为什么独立成表而不是给 ``documents`` 加 progress 列**：``create_all()`` 只建
    新表、**不会给已存在的表加列**，而 SQLite 分支没有迁移路径（本项目踩过
    ``no column named user_id``）。新表由 ``create_all`` 直接建，零迁移痛苦。

    **重试复用同一行**（不新建 attempt 记录）：``document_id`` 唯一约束保证一份文档
    只有一个 job，重试时原地重置 ``stage`` / ``done_units`` / ``error`` / ``started_at``。
    取舍理由：查询与前端取数都只需"这份文档现在怎么样了"，不需要 attempt 历史。

    ``stage`` 取值：pending → parsing → chunking → embedding → indexing → done | failed。
    终态 = ``done`` / ``failed``（``TERMINAL_STAGES``）。
    """

    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_ingestion_job_document"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    kb_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    done_units: Mapped[int] = mapped_column(Integer, default=0)
    total_units: Mapped[int] = mapped_column(Integer, default=0)  # chunking 完成后回填
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QaLog(Base):
    """问答日志（G4）：每次问答的成功事实，供历史查询与死文档检测。

    随知识库级联删除（KB 删除后日志无意义），保持测试 cleanup 可直接删 KB。
    """

    __tablename__ = "qa_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kb_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    query: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    hit_doc_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON 数组
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Conversation(Base):
    """会话（J2 会话持久化）：一个知识库下的一次多轮对话，属某个用户私有。

    随知识库级联删除；会话删除级联删除其消息（cascade=all, delete-orphan）。
    """

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    kb_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="ChatMessage.id"
    )


class ChatMessage(Base):
    """会话内一条消息（J2）：role=user|assistant，assistant 消息存 sources（JSON）。

    会话级联删除；随知识库级联删除。
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[str] = mapped_column(Text, default="[]")  # JSON：assistant 引用来源
    rewritten_query: Mapped[str] = mapped_column(String(1000), default="")  # assistant 改写词
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class MessageFeedback(Base):
    """答案反馈（K5）：对一条 ChatMessage 的赞/踩 + 可选原因标签。

    独立成表而不是给 chat_messages 加列：create_all 零迁移；(message_id, user_id)
    唯一约束保证同一条消息一个人只有一票（重复提交是更新，不是插新行）。
    级联链与消息一致：会话 / 知识库删除后反馈无意义，随行删除。
    """

    __tablename__ = "message_feedback"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_message_feedback"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    rating: Mapped[str] = mapped_column(String(10))  # up | down
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class QueryCache(Base):
    """问答缓存（K6）：kb 内规范化问题 → 答案 + 来源，精确 / 语义两级命中。

    - (kb_id, query_norm, mode, top_k, model) 唯一：同库同问**同参数**只有一条缓存
      —— top_k / model 必须参与键：否则对话页的「条数」和「模型切换」会被缓存
      静默吞掉（测试照出后修正，见 progress-log K6）；
    - 语义命中同样限定同 mode + top_k + model，只对**问法**做余弦宽容；
    - ``embedding`` 存 float32 packed BLOB，语义命中时现算**查询**向量与条目比余弦，
      不需要把缓存向量放进向量库（几百条规模纯 Python 余弦 <10ms）；
    - 失效是 kb 级全量：文档增删由应用层清空该库（见 app/rag/query_cache.py），
      删库走 FK 级联；不设 TTL；
    - ``hit_count`` / ``updated_at`` 供命中率观测与 LRU 淘汰。
    """

    __tablename__ = "query_cache"
    __table_args__ = (
        UniqueConstraint(
            "kb_id", "query_norm", "mode", "top_k", "model", name="uq_query_cache"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kb_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    query_norm: Mapped[str] = mapped_column(String(2000))  # 折叠空白 + lower 的规范化键
    query: Mapped[str] = mapped_column(Text)  # 首次写入时的原始问句（展示 / 调试）
    mode: Mapped[str] = mapped_column(String(20))  # vector | hybrid
    top_k: Mapped[int] = mapped_column(Integer)
    answer: Mapped[str] = mapped_column(Text)
    sources: Mapped[str] = mapped_column(Text, default="[]")  # JSON：SourceRef 字段
    rewritten_query: Mapped[str] = mapped_column(String(1000), default="")
    citation_issues: Mapped[str] = mapped_column(Text, default="[]")  # JSON：越界编号
    model: Mapped[str] = mapped_column(String(100), default="")  # 生成时的模型（观测用）
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
