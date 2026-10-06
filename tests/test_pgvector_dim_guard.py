"""向量表维度护栏（`PgVectorStore._reconcile_dim`）—— 离线、不依赖 PostgreSQL。

**为什么需要这条护栏**：`embedding vector({dim})` 的维度只在建表时确定，而
`CREATE TABLE IF NOT EXISTS` 遇到已存在的表直接跳过。本项目**同时有两条运行路径**：
Docker 交付用 mock（64 维）、本地开发用百炼 `qwen3.7-text-embedding`（1024 维）。
两条路径切来切去时，表里还是上一个维度的结构，症状是写入抛

    DataError: expected 1024 dimensions, not 64

从 SQLAlchemy 栈底冒出来 —— 完全看不出是"表结构没跟上配置"。2026-10-06 我在跑
PG-gated 回归时就被它绊了一整轮（先是真实 embedding 建了 1024 的表，之后换 mock
跑，检索全部返回空、20 个用例集体变红，看着像功能坏了）。

处置分两种，**这个测试钉的就是这个分界**：

- 空表 → 自动重建（换维度没有数据要保，重建是唯一正确解）
- 非空表 → **报错，绝不自动 drop**（静默毁掉已灌进的语料是不可接受的破坏性动作）

用桩连接而非真 PG：要验的是"看到什么维度就做什么决定"，与 PostgreSQL 无关，
放成 PG-gated 只会让这条护栏在 CI（无 PG）里永不生效 —— 那是 #43 栽过的同一个坑。
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.storage.pgvector_store import PgVectorStore


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeConn:
    """按 SQL 意图返回预设值，并记录执行过哪些语句。"""

    def __init__(self, existing_type: str | None, row_count: int = 0) -> None:
        self.existing_type = existing_type
        self.row_count = row_count
        self.sqls: list[str] = []

    def exec_driver_sql(self, sql: str, params=None) -> _FakeResult:
        self.sqls.append(sql.strip())
        lowered = sql.lower()
        if "format_type" in lowered:
            return _FakeResult(self.existing_type)
        if "count(*)" in lowered:
            return _FakeResult(self.row_count)
        return _FakeResult(None)

    @property
    def dropped(self) -> bool:
        return any(s.lower().startswith("drop table") for s in self.sqls)


@pytest.fixture()
def store() -> PgVectorStore:
    # 不连库：__init__ 只存 settings，engine 是惰性的。
    # _env_file=None 让本测试与本机 .env 完全解耦。
    return PgVectorStore(Settings(_env_file=None))


def test_table_absent_is_a_noop(store: PgVectorStore) -> None:
    """首启路径：表还不存在，什么都不该做（由随后的 CREATE 建表）。"""
    conn = _FakeConn(existing_type=None)
    store._reconcile_dim(conn, 1024)
    assert not conn.dropped


def test_matching_dim_is_a_noop(store: PgVectorStore) -> None:
    """维度一致 —— 最常见路径，必须是零副作用。"""
    conn = _FakeConn(existing_type="vector(1024)")
    store._reconcile_dim(conn, 1024)
    assert not conn.dropped


def test_mismatched_dim_on_empty_table_rebuilds(store: PgVectorStore) -> None:
    """空表 + 维度不符 → 自动重建，用户不必手工敲 DDL。"""
    conn = _FakeConn(existing_type="vector(64)", row_count=0)
    store._reconcile_dim(conn, 1024)
    assert conn.dropped


def test_mismatched_dim_with_data_raises_and_never_drops(store: PgVectorStore) -> None:
    """非空表 + 维度不符 → 报错；**绝不能自动 drop**，那会静默丢掉已灌的语料。

    报错信息要能直接指导处置（改回配置 / 清表重灌），否则用户只能看到
    一句 `expected 1024 dimensions, not 64` 去猜。
    """
    conn = _FakeConn(existing_type="vector(1024)", row_count=42)
    with pytest.raises(RuntimeError) as exc:
        store._reconcile_dim(conn, 64)
    assert not conn.dropped
    msg = str(exc.value)
    assert "1024" in msg and "64" in msg  # 两个维度都要报出来
    assert "42" in msg  # 涉及多少行数据
    assert "DROP TABLE" in msg  # 给出可执行的处置动作


def test_unparseable_column_type_is_tolerated(store: PgVectorStore) -> None:
    """列类型解析不出维度时（理论上不该发生）保守放行，不误伤正常路径。"""
    conn = _FakeConn(existing_type="vector")
    store._reconcile_dim(conn, 1024)
    assert not conn.dropped
