"""端到端冒烟测试：摄取示例文档 + 提问，打印答案与引用来源。

用法：
    python scripts/demo.py                     # 默认问题
    python scripts/demo.py "年假怎么计算？"     # 自定义问题

不配置 .env 时自动使用 mock 供应商（无需 API Key），全链路本地可跑通。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Windows 控制台默认 GBK，重配置为 UTF-8 避免中英文混合打印崩溃
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from app.config import get_settings
from app.core.models import Document, User
from app.ingestion.pipeline import IngestionPipeline
from app.rag.pipeline import RagPipeline
from app.storage.db import get_db

DEFAULT_QUESTION = "员工休病假超过三天需要提交什么材料？"


def _ensure_demo_user(session_factory) -> int:
    """确保存在 demo 用户（脚本用），返回 user_id。"""
    with get_db(session_factory) as db:
        user = db.query(User).filter(User.username == "_demo").first()
        if user is None:
            user = User(username="_demo", hashed_password="")
            db.add(user)
            db.flush()
        return user.id


def main() -> None:
    settings = get_settings()
    if settings.rag_provider == "mock":
        print("[!] 当前使用 mock 供应商（无真实模型）。复制 .env.example 为 .env 并填写 API Key 后可使用真实模型。\n")

    ingest = IngestionPipeline()
    rag = RagPipeline()

    user_id = _ensure_demo_user(ingest.session_factory)
    kb = ingest.get_or_create_kb("demo", "演示知识库", user_id=user_id)
    print(f"知识库: kb#{kb.id}「{kb.name}」")

    sample = ROOT / "docs" / "sample.md"
    _ingest_once(ingest, kb.id, sample)

    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    print(f"\n问题: {question}")

    t0 = time.perf_counter()
    result = rag.ask(kb.id, question)
    elapsed = time.perf_counter() - t0

    print(f"\n答案（耗时 {elapsed:.2f}s）:")
    print(result.answer)
    print("\n引用来源:")
    for s in result.sources:
        preview = s.content.replace("\n", " ")[:80]
        print(f"  [{s.filename}] chunk#{s.chunk_index} 相似度 {s.score:.3f}  {preview}...")


def _ingest_once(ingest: IngestionPipeline, kb_id: int, path: Path) -> None:
    """同一文档已成功索引则跳过，避免重复摄取。"""
    with ingest.session_factory() as db:
        exists = (
            db.query(Document)
            .filter(
                Document.kb_id == kb_id,
                Document.filename == path.name,
                Document.status == "indexed",
            )
            .first()
        )
    if exists is not None:
        print(f"文档已索引，跳过: {path.name}")
        return

    print(f"摄取文档: {path.name} ...")
    doc = ingest.ingest_file(kb_id, path)
    if doc.status == "failed":
        print(f"✗ 摄取失败: {doc.error}")
        sys.exit(1)
    print(f"✓ 已索引 {doc.chunk_count} 个切片 (doc#{doc.id})")


if __name__ == "__main__":
    main()
