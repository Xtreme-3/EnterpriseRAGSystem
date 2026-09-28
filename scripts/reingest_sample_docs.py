"""把 ``docs/sample-docs`` 下的语料灌进知识库（走正在运行的 API，不直连数据库）。

**为什么走 API 而不是直接写库**：摄取链路上有解析、切片、embedding、写向量库四步，
直接写 SQLite 会漏掉向量库那一侧（chunks 表有内容、检索却查不到）。API 是唯一
保证两侧一致的入口。

**为什么必须先删后传**：``documents`` 表按 filename 记录，重新上传同名文件不会覆盖
旧切片，只会多出一份 —— 检索时会同时召回新旧两版，答案自相矛盾。所以默认先清空目标
知识库的全部文档（级联删除 chunks 与向量）；确实想增量追加时用 ``--keep-existing``。

用法::

    # 1) 最常用：按分组灌库（推荐，分组见下方 CORPUS_GROUPS）
    python scripts/reingest_sample_docs.py --preset business
    python scripts/reingest_sample_docs.py --preset hr-finance

    # 2) 先看会删什么、传什么，不改数据
    python scripts/reingest_sample_docs.py --preset hr-finance --dry-run

    # 3) 不用分组时，手工指定库与文件
    python scripts/reingest_sample_docs.py --kb 1 --files "全球优选_产品手册.pdf"
    python scripts/reingest_sample_docs.py --kb-name "人事与财务制度" --create \\
        --files "全球优选_员工手册.docx" \\
        --files "全球优选_财务报销与差旅管理办法.md"

``--files`` 接受相对 ``--src`` 的 glob（如 ``"*手册*.md"``）；不传时默认 ``*.pdf``。

**为什么要有 --preset**：语料目录是平的，而 ``*.pdf`` 现在能匹配到 7 份。如果对着
``--kb 1`` 直接跑默认 glob，会把本该进 kb_2 的人事 / 财务 / 税务三份也灌进 kb_1，
污染「业务制度」这个库的检索面。分组把「哪份进哪个库」固化在代码里，不靠人记。

运行前确保 API 已起（``uvicorn app.main:app``）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "sample-docs"

#: 语料分组：一份语料只属于一个库，避免 --kb 1 配默认 glob 时把别的库的料一起灌进去。
#: 新增语料时先改这里，再生成产物（scripts/generate_sample_docs.py）。
CORPUS_GROUPS: dict[str, dict] = {
    # kb_1「演示知识库」：对外业务制度
    "business": {
        "kb": 1,
        "files": [
            "全球优选_产品手册.pdf",
            "全球优选_供应商管理制度.pdf",
            "全球优选_跨境物流指南.pdf",
            "全球优选_跨境售后政策.pdf",
        ],
    },
    # kb_2「人事与财务制度」：内部管理规章，故意混用 docx / md / pdf 三种格式
    "hr-finance": {
        "kb_name": "人事与财务制度",
        "create": True,
        "files": [
            "全球优选_员工手册.docx",
            "全球优选_财务报销与差旅管理办法.md",
            "全球优选_出口退税与跨境税务合规制度.pdf",
        ],
    },
}

# 必须与 app/ingestion/parsers.py 的 SUPPORTED_EXTS 对齐。显式写死而不查表，
# 是因为服务端只把扩展名白名单用于校验、并不校验 content-type，
# 而 Windows 注册表查出来的 .md 类型经常是 application/octet-stream。
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
}


def _login(client: httpx.Client, user: str, password: str) -> str:
    resp = client.post("/api/auth/login", json={"username": user, "password": password})
    if resp.status_code != 200:
        raise SystemExit(f"登录失败 HTTP {resp.status_code}: {resp.text}")
    return resp.json()["access_token"]


def _content_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext not in CONTENT_TYPES:
        raise SystemExit(f"不支持的扩展名 {ext}（{path.name}）；可用：{', '.join(sorted(CONTENT_TYPES))}")
    return CONTENT_TYPES[ext]


def _collect_files(src: Path, patterns: list[str]) -> list[Path]:
    """按 glob 收集文件，去重后按名字排序。

    模式里不含通配符时按精确文件名处理（找不到就直接报错，而不是静默 0 份）。
    """
    found: dict[str, Path] = {}
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            matches = sorted(src.glob(pattern))
            if not matches:
                raise SystemExit(f"模式 {pattern!r} 在 {src} 下没匹配到任何文件")
        else:
            target = src / pattern
            if not target.is_file():
                raise SystemExit(f"找不到文件 {target}")
            matches = [target]
        for m in matches:
            found[str(m.resolve())] = m
    return sorted(found.values(), key=lambda p: p.name)


def _resolve_kb(
    client: httpx.Client, headers: dict, args: argparse.Namespace
) -> tuple[int | None, str]:
    """确定目标知识库，返回 (kb_id, 来源说明)。

    ``kb_id`` 为 None 表示「dry-run 下按需新建但尚未真的建」，调用方须跳过后续库内操作。
    --create 时按名字查找，缺失则新建。
    """
    kbs = client.get("/api/kbs", headers=headers)
    if kbs.status_code != 200:
        raise SystemExit(f"读取知识库列表失败 HTTP {kbs.status_code}: {kbs.text}")
    listing = kbs.json()

    if args.kb is not None:
        match = [k for k in listing if k["id"] == args.kb]
        name = match[0]["name"] if match else "(不在当前账号可见列表中)"
        return args.kb, f"--kb {args.kb}（{name}）"

    assert args.kb_name, "argparse 已保证 --kb 与 --kb-name 二选一"
    match = [k for k in listing if k["name"] == args.kb_name]
    if match:
        return match[0]["id"], f'--kb-name "{args.kb_name}" 命中已有库'

    if not args.create:
        names = "、".join(k["name"] for k in listing) or "（无）"
        raise SystemExit(
            f'没找到名为 "{args.kb_name}" 的知识库，当前可见：{names}。'
            f"加 --create 可直接建。"
        )

    if args.dry_run:
        # dry-run 不产生副作用：不建库，交给调用方只打印计划
        return None, f'--create 将新建 "{args.kb_name}"（dry-run 未实际创建）'

    resp = client.post("/api/kbs", headers=headers, json={"name": args.kb_name})
    if resp.status_code != 201:
        raise SystemExit(f"建库失败 HTTP {resp.status_code}: {resp.text}")
    body = resp.json()
    return body["id"], f'--create 新建 "{args.kb_name}"'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="清空（或新建）知识库并重新上传 sample-docs 下的语料",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="后端地址")
    parser.add_argument(
        "--preset",
        choices=sorted(CORPUS_GROUPS),
        default=None,
        help="语料分组，自动带上目标库与文件清单（推荐）",
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--kb", type=int, default=None, help="目标知识库 ID")
    target.add_argument("--kb-name", default=None, help="按名字定位知识库（配合 --create 可建）")
    parser.add_argument("--create", action="store_true", help="--kb-name 不存在时创建该库")
    parser.add_argument(
        "--files",
        action="append",
        default=None,
        metavar="GLOB",
        help="要上传的文件（相对 --src 的 glob 或精确文件名），可重复；默认 *.pdf",
    )
    parser.add_argument("--src", default=str(SRC), help=f"语料目录，默认 {SRC}")
    parser.add_argument(
        "--keep-existing",
        action="store_true",
        help="不删除库内已有文档，只做增量追加（同名文件会产生重复切片，慎用）",
    )
    parser.add_argument("--user", default="demo")
    parser.add_argument("--password", default="demo123456")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不改数据")
    args = parser.parse_args(argv)

    # preset 只填「没显式给出的」参数，命令行上写的永远优先
    if args.preset:
        group = CORPUS_GROUPS[args.preset]
        if args.kb is None and "kb" in group:
            args.kb = group["kb"]
        if args.kb_name is None and "kb_name" in group:
            args.kb_name = group["kb_name"]
        if not args.create:
            args.create = bool(group.get("create"))
        if args.files is None:
            args.files = list(group["files"])

    if args.kb is None and args.kb_name is None:
        args.kb = 1  # 历史默认行为：kb_1

    src = Path(args.src)
    if not src.is_dir():
        print(f"语料目录不存在：{src}")
        return 1

    files = _collect_files(src, args.files or ["*.pdf"])
    for f in files:
        _content_type(f)  # 提前把不支持的扩展名挑出来，别等传到一半才炸

    # 沙箱 / 本机代理会把 127.0.0.1 也劫走，显式声明不使用代理
    with httpx.Client(base_url=args.api, timeout=300.0, trust_env=False) as client:
        token = _login(client, args.user, args.password)
        headers = {"Authorization": f"Bearer {token}"}

        kb_id, how = _resolve_kb(client, headers, args)
        label = f"kb_{kb_id}" if kb_id is not None else "（尚未创建）"
        print(f"目标知识库 {label}（{how}）")

        old: list[dict] = []
        if kb_id is not None:
            existing = client.get(f"/api/kbs/{kb_id}/documents", headers=headers)
            if existing.status_code != 200:
                raise SystemExit(f"读取文档列表失败 HTTP {existing.status_code}: {existing.text}")
            old = existing.json()

        print(f"现有 {len(old)} 份文档：")
        for doc in old:
            print(f"  - #{doc['id']} {doc['filename']} ({doc.get('chunk_count')} 切片)")
        if not old:
            print("  （空）")

        print(f"\n将上传 {len(files)} 份：")
        for f in files:
            print(f"  - {f.name}  [{_content_type(f)}]")

        if args.keep_existing and old:
            print("\n注意：--keep-existing 已开启，同名旧文档不会被删除，检索时可能召回新旧两版。")

        if args.dry_run:
            print("\n[dry-run] 未改动任何数据。去掉 --dry-run 即执行。")
            return 0

        if kb_id is None:  # 理论上不可达：非 dry-run 时 _resolve_kb 一定给出 id
            raise SystemExit("内部错误：目标知识库未解析出 ID")

        if not args.keep_existing:
            for doc in old:
                resp = client.delete(f"/api/documents/{doc['id']}", headers=headers)
                if resp.status_code not in (200, 204):
                    raise SystemExit(f"删除 #{doc['id']} 失败 HTTP {resp.status_code}: {resp.text}")
                print(f"已删除 #{doc['id']} {doc['filename']}")

        failed: list[str] = []
        for f in files:
            with f.open("rb") as fh:
                resp = client.post(
                    f"/api/kbs/{kb_id}/documents",
                    headers=headers,
                    files={"file": (f.name, fh, _content_type(f))},
                )
            if resp.status_code != 201:
                failed.append(f.name)
                print(f"上传 {f.name} 失败 HTTP {resp.status_code}: {resp.text}")
                continue
            body = resp.json()
            print(f"已上传 {f.name} → #{body['id']}，{body.get('chunk_count')} 切片")

        final = client.get(f"/api/kbs/{kb_id}/documents", headers=headers).json()
        total = sum(d.get("chunk_count") or 0 for d in final)
        print(f"\n完成：kb_{kb_id} 现有 {len(final)} 份文档 / {total} 个切片")
        if failed:
            print(f"失败 {len(failed)} 份：{'、'.join(failed)}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
