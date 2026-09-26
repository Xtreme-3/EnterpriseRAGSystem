"""换 embedding 后重建向量集合（K0）。

**为什么需要它**：向量集合的维度在「建集合时」就钉死了。换 embedding 提供商后原集合
不可复用——chroma 会直接报
``Collection expecting embedding with dimension of 64, got 1024``。

但只要元数据库里还有切片原文（``Chunk.content``），就**不必重新上传原始文档**：
本脚本直接从 SQLite 读出全部切片，用当前配置的 embedding 重新编码后写回向量库。

**运行前请先停掉 API 服务**（SQLite 与 chroma 目录都可能被占用）。

用法::

    python scripts/reindex_embeddings.py --dry-run   # 只打印计划，不动数据（建议先跑）
    python scripts/reindex_embeddings.py             # 重建全部知识库
    python scripts/reindex_embeddings.py --kb 1      # 只重建 kb_id=1

重建后建议跑一次 ``POST /api/kbs/{id}/inspect`` 或前端质检台，确认检索能出结果。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.providers.factory import build_embedding  # noqa: E402
from app.storage.vector_store import ChunkToIndex, build_vector_store  # noqa: E402


def _load_chunks(db_path: Path, kb_id: int | None) -> list[tuple]:
    """从元数据库读出 (kb_id, document_id, chunk_index, content)。"""
    sql = "select kb_id, document_id, chunk_index, content from chunks"
    params: tuple = ()
    if kb_id is not None:
        sql += " where kb_id = ?"
        params = (kb_id,)
    sql += " order by kb_id, document_id, chunk_index"
    with sqlite3.connect(db_path) as con:
        return con.execute(sql, params).fetchall()


def main() -> int:
    ap = argparse.ArgumentParser(description="用当前 embedding 重新编码全部切片")
    ap.add_argument("--kb", type=int, default=None, help="只重建该 kb_id（默认全部）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写向量库")
    args = ap.parse_args()

    settings = get_settings()
    if not settings.db_path.exists():
        print(f"元数据库不存在：{settings.db_path}")
        return 1

    rows = _load_chunks(settings.db_path, args.kb)
    if not rows:
        print("没有切片可重建（chunks 表为空）。")
        return 0

    embedding = build_embedding(settings)
    store = build_vector_store(settings)

    grouped: dict[int, list[tuple]] = {}
    for r in rows:
        grouped.setdefault(r[0], []).append(r)

    print(f"embedding : {type(embedding).__name__} / {settings.embedding_model} / dim={embedding.dim}")
    print(f"向量库    : {settings.vector_store}")
    print(f"待重建    : {len(grouped)} 个知识库 / {len(rows)} 个切片")
    print()

    for kb_id in sorted(grouped):
        items = grouped[kb_id]
        if args.dry_run:
            print(f"  kb_id={kb_id}: {len(items)} 个切片  [dry-run，未写入]")
            continue

        # 旧集合的维度已不可用，必须整体删掉重建
        try:
            store.delete_collection(kb_id)
        except Exception as exc:  # noqa: BLE001 - 集合本来不存在时忽略
            print(f"  kb_id={kb_id}: 旧集合不存在或删除失败（继续）: {exc}")

        store.ensure_collection(kb_id, embedding.dim)
        vectors = embedding.embed([it[3] for it in items])
        if len(vectors) != len(items):
            print(f"  kb_id={kb_id}: 向量数 {len(vectors)} ≠ 切片数 {len(items)}，中止")
            return 1
        store.add(
            kb_id,
            [
                ChunkToIndex(
                    id=f"{it[1]}:{it[2]}",
                    content=it[3],
                    vector=v,
                    document_id=it[1],
                    kb_id=it[0],
                    chunk_index=it[2],
                )
                for it, v in zip(items, vectors, strict=True)
            ],
        )
        print(f"  kb_id={kb_id}: {len(items)} 个切片 → 已写入（dim={embedding.dim}）")

    if args.dry_run:
        print("\n[dry-run] 未改动任何数据。去掉 --dry-run 即执行。")
    else:
        print("\n完成。建议触发一次检索验证：POST /api/kbs/{id}/inspect")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
