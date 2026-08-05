"""SQLite 元数据库：engine / session 工厂与建表。"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.core.models import Base


def init_db(settings: Settings | None = None) -> tuple[object, sessionmaker]:
    """确保 data 目录与表存在，返回 (engine, session_factory)。"""
    s = settings or get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        s.sqlite_url,
        # SQLite 多线程读取（FastAPI 阶段需要）
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
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
