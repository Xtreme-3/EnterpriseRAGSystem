"""把 ``docs/sample-docs/*.pdf`` 重新灌进知识库（走正在运行的 API，不直连数据库）。

**为什么走 API 而不是直接写库**：摄取链路上有解析、切片、embedding、写向量库四步，
直接写 SQLite 会漏掉向量库那一侧（chunks 表有内容、检索却查不到）。API 是唯一
保证两侧一致的入口。

**为什么必须先删后传**：``documents`` 表按 filename 记录，重新上传同名文件不会覆盖
旧切片，只会多出一份 —— 检索时会同时召回新旧两版，答案自相矛盾。所以默认先清空目标
知识库的全部文档（级联删除 chunks 与向量）。

用法::

    python scripts/reingest_sample_docs.py --dry-run          # 只看会删什么、传什么
    python scripts/reingest_sample_docs.py --kb 1             # 清空 kb_1 后重传 4 份 PDF
    python scripts/reingest_sample_docs.py --user demo --password demo123456

运行前确保 API 已起（``uvicorn app.main:app``）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "sample-docs"


def _login(client: httpx.Client, user: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"username": user, "password": password})
    if resp.status_code != 200:
        raise SystemExit(f"登录失败 HTTP {resp.status_code}: {resp.text}")
    return resp.json()["access_token"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清空知识库并重新上传 sample-docs 下的 PDF")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="后端地址")
    parser.add_argument("--kb", type=int, default=1, help="目标知识库 ID")
    parser.add_argument("--user", default="demo")
    parser.add_argument("--password", default="demo123456")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不改数据")
    args = parser.parse_args(argv)

    pdfs = sorted(SRC.glob("*.pdf"))
    if not pdfs:
        print(f"{SRC} 下没有 PDF，先跑 scripts/generate_sample_docs.py")
        return 1

    # 沙箱 / 本机代理会把 127.0.0.1 也劫走，显式声明不使用代理
    with httpx.Client(base_url=args.api, timeout=300.0, trust_env=False) as client:
        token = _login(client, args.user, args.password)
        headers = {"Authorization": f"Bearer {token}"}

        existing = client.get(f"/api/kbs/{args.kb}/documents", headers=headers)
        if existing.status_code != 200:
            raise SystemExit(f"读取文档列表失败 HTTP {existing.status_code}: {existing.text}")
        old = existing.json()

        print(f"目标知识库 kb_{args.kb}，现有 {len(old)} 份文档：")
        for doc in old:
            print(f"  - #{doc['id']} {doc['filename']} ({doc.get('chunk_count')} 切片)")
        print(f"\n将上传 {len(pdfs)} 份 PDF：")
        for pdf in pdfs:
            print(f"  - {pdf.name}")

        if args.dry_run:
            print("\n[dry-run] 未改动任何数据。去掉 --dry-run 即执行。")
            return 0

        for doc in old:
            resp = client.delete(f"/api/documents/{doc['id']}", headers=headers)
            if resp.status_code not in (200, 204):
                raise SystemExit(f"删除 #{doc['id']} 失败 HTTP {resp.status_code}: {resp.text}")
            print(f"已删除 #{doc['id']} {doc['filename']}")

        for pdf in pdfs:
            with pdf.open("rb") as fh:
                resp = client.post(
                    f"/api/kbs/{args.kb}/documents",
                    headers=headers,
                    files={"file": (pdf.name, fh, "application/pdf")},
                )
            if resp.status_code != 201:
                raise SystemExit(f"上传 {pdf.name} 失败 HTTP {resp.status_code}: {resp.text}")
            body = resp.json()
            print(f"已上传 {pdf.name} → #{body['id']}，{body.get('chunk_count')} 切片")

        final = client.get(f"/api/kbs/{args.kb}/documents", headers=headers).json()
        total = sum(d.get("chunk_count") or 0 for d in final)
        print(f"\n完成：kb_{args.kb} 现有 {len(final)} 份文档 / {total} 个切片")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
