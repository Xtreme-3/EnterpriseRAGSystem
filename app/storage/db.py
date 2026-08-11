"""元数据库：engine / session 工厂与建表。

支持 SQLite（默认）和 PostgreSQL（pgvector 模式下一套库管元数据+向量）。
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.core.models import Base


def init_db(settings: Settings | None = None) -> tuple[object, sessionmaker]:
    """确保 data 目录（SQLite）或数据库（PG）存在并建表，返回 (engine, session_factory)。"""
    s = settings or get_settings()

    if s.vector_store == "pgvector":
        # PostgreSQL 模式：元数据与向量共库
        engine = create_engine(s.database_url)
    else:
        # SQLite 模式（默认）：本地文件持久化
        s.data_dir.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            s.sqlite_url,
            connect_args={"check_same_thread": False},
        )

    Base.metadata.create_all(engine)

    # I2 RBAC：老 PG 库 users 表补 role 列（幂等；create_all 不会 ALTER 已存在的表，
    # 新表 knowledge_base_members 由 create_all 自动建）。SQLite 测试用全新库，天然包含。
    if s.vector_store == "pgvector":
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                "role VARCHAR(20) NOT NULL DEFAULT 'user'"
            )

    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return engine, session_factory


@contextmanager
def get_db(session_factory: sessionmaker) -> Iterator[Session]:
    """上下文管理器：自动提交/回滚/关闭。"""
    session: Session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
