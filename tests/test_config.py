"""Settings.database_url 的 DSN 契约（bugfix #43 回归测试）。

**背景**：Docker 交付链路第一次被真跑时（2026-09-28），`api` 容器启动即崩：

    ModuleNotFoundError: No module named 'psycopg'

本地 venv 装的是 SQLAlchemy 2.0.51，容器里解析到 2.1.1。裸 `postgresql://`
的默认 DBAPI 会随版本漂移 —— 2.0 走 psycopg2，2.1 起改走 psycopg（v3），
而 pyproject 只声明了 psycopg2-binary。

**为什么此前没人发现**：本地 `.env` 是 `VECTOR_STORE=chroma`，pgvector 这条
代码路径在本机从未被执行；而 tests/test_pgvector_store.py 在 PG 不可达时整模块
skip，驱动缺失也被那个宽泛的 except 吞掉，表现为"环境不可用"而非失败。

所以这里必须有一条**离线、不依赖 PG** 的断言，把"URL 写死驱动"这件事钉住。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from app.config import Settings


def _pg_settings() -> Settings:
    """构造 pgvector 配置，显式绕开 .env，保证结果只由代码决定。"""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        vector_store="pgvector",
        pg_host="db",
        pg_port=5432,
        pg_user="postgres",
        pg_password="ragpass",
        pg_database="ragdb",
    )


def test_database_url_pins_psycopg2_driver() -> None:
    """DSN 必须显式带 `+psycopg2`，不能是裸 `postgresql://`。"""
    url = _pg_settings().database_url

    assert url.startswith("postgresql+psycopg2://"), (
        f"驱动未被写死，SQLAlchemy 升级到 2.1+ 后会改走 psycopg v3 并崩溃：{url}"
    )
    # 反向断言：不允许出现无驱动的 scheme
    assert not url.startswith("postgresql://")


def test_database_url_interpolates_all_pg_fields() -> None:
    """用户名/密码/主机/端口/库名逐项落到 DSN 上。"""
    url = _pg_settings().database_url

    assert url == "postgresql+psycopg2://postgres:ragpass@db:5432/ragdb"


def test_database_url_engine_resolves_declared_driver() -> None:
    """create_engine 必须能解析出 DBAPI —— 即依赖清单真的满足这个方言。

    这条不需要真实 PG 可达：create_engine 只做导入，不建连接。
    在 SQLAlchemy 2.1 + 未装 psycopg 的环境里，若 DSN 少了 `+psycopg2`
    这里就会抛 ModuleNotFoundError，正是 #43 的现场。
    """
    try:
        engine = create_engine(_pg_settings().database_url)
    except (ImportError, ModuleNotFoundError) as exc:  # pragma: no cover - 回归失败路径
        pytest.fail(
            f"依赖清单无法满足 database_url 的方言，容器里会启动即崩：{exc}"
        )

    # 驱动名确认，避免"能导入但导错了库"
    assert engine.dialect.driver == "psycopg2"
