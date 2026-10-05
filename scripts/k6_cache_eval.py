"""K6 问答缓存真环境评测：语义命中阈值（默认 0.95）的护栏。

用法（需 .env 配真实供应商；VECTOR_STORE=chroma 走 SQLite，无需 PostgreSQL）：

    python scripts/k6_cache_eval.py --kb 1 --cross-kb 2

四类探针，前两类必须全对，否则退出码 1：

1. 同义对   —— 同意图的改写问法**应**语义命中（返回与原问相同的缓存答案）
2. 易混对   —— 同域但意图不同的问法**不许**命中（这是语义缓存唯一的实质风险）
3. 跨库串扰 —— kb_1 内容的问法投到另一个库，不许命中那边的缓存
4. 延迟对照 —— 未命中（走完整链路）vs 命中（缓存）的单问耗时

评测结束后会清空两个库的缓存，不留测试数据。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.rag.pipeline import RagPipeline  # noqa: E402
from app.rag.query_cache import cosine  # noqa: E402

# 同意图改写对：(原问, 改写问) —— 改写问必须命中原问的缓存
SAME_INTENT = [
    ("供应商准入需要满足哪些条件？", "供应商入驻要满足什么门槛？"),
    ("出差住宿标准是多少？", "出差住宿可以报销多少钱？"),
    ("皮具类产品的五金件有什么要求？", "皮具五金件的标准是什么？"),
]

# 易混对：同域、用词相近，但意图不同 —— 不许误命中
DIFF_INTENT = [
    ("报销时效是多少天？", "报销的审批流程是怎样的？"),
    ("供应商验厂多少分会被淘汰？", "供应商验厂需要提前准备什么？"),
]

# 跨库串扰探针：问题本身是 kb_1 的内容，投到别的库去问
CROSS_KB_QUERY = "美国站的退货窗口是多少天？"


def _sim(rag: RagPipeline, a: str, b: str) -> float:
    return cosine(
        rag.embedding.embed([a])[0],
        rag.embedding.embed([b])[0],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb", type=int, default=1, help="主评测知识库 id")
    parser.add_argument("--cross-kb", type=int, default=2, help="跨库串扰探针的知识库 id")
    args = parser.parse_args()

    rag = RagPipeline()
    failures: list[str] = []
    rows: list[str] = []

    print(f"== K6 缓存评测 kb={args.kb}（阈值 {rag.settings.cache_semantic_threshold}）==\n")

    # 1. 同义对：改写问应语义命中
    for orig, para in SAME_INTENT:
        rag.cache.invalidate_kb(args.kb)
        t0 = time.perf_counter()
        r1 = rag.ask(args.kb, orig)
        t_miss = time.perf_counter() - t0
        t0 = time.perf_counter()
        r2 = rag.ask(args.kb, para)
        t_hit = time.perf_counter() - t0
        score = _sim(rag, orig, para)
        ok = (not r1.cache_hit) and r2.cache_hit and r2.answer == r1.answer
        if not ok:
            failures.append(f"同义对未按预期命中：{orig!r} → {para!r} score={score:.4f}")
        rows.append(
            f"| 同义 | {orig} → {para} | {score:.4f} | "
            f"{'✅ 命中' if r2.cache_hit else '❌ 未命中'} | {t_miss:.1f}s → {t_hit:.1f}s |"
        )

    # 2. 易混对：不同意图不许命中
    for a, b in DIFF_INTENT:
        rag.cache.invalidate_kb(args.kb)
        rag.ask(args.kb, a)
        rb = rag.ask(args.kb, b)
        score = _sim(rag, a, b)
        ok = not rb.cache_hit
        if not ok:
            failures.append(f"易混对误命中：{a!r} → {b!r} score={score:.4f}")
        rows.append(
            f"| 易混 | {a} → {b} | {score:.4f} | "
            f"{'✅ 未命中' if not rb.cache_hit else '❌ 误命中'} | - |"
        )

    # 3. 跨库串扰：先给目标库种两条它自己领域的缓存，再投 kb_1 的问题过去
    rag.cache.invalidate_kb(args.kb)
    rag.cache.invalidate_kb(args.cross_kb)
    rag.ask(args.kb, CROSS_KB_QUERY)  # kb_1 里这是有答案的问题，写入 kb_1 缓存
    rag.ask(args.cross_kb, "员工年假有多少天？")  # 给目标库种缓存
    rb = rag.ask(args.cross_kb, CROSS_KB_QUERY)  # 拿同问投到别的库
    ok = not rb.cache_hit
    if not ok:
        failures.append(f"跨库串扰误命中：{CROSS_KB_QUERY!r} 在 kb={args.cross_kb} 命中了缓存")
    rows.append(
        f"| 串扰 | {CROSS_KB_QUERY}（投到 kb={args.cross_kb}） | - | "
        f"{'✅ 未命中' if not rb.cache_hit else '❌ 误命中'} | - |"
    )

    # 收尾：不留评测缓存
    rag.cache.invalidate_kb(args.kb)
    rag.cache.invalidate_kb(args.cross_kb)

    print("| 类型 | 问法对 | 余弦 | 结果 | 耗时 |")
    print("|---|---|---|---|---|")
    for row in rows:
        print(row)

    report = [
        "# K6 问答缓存评测报告",
        "",
        f"- 日期：{time.strftime('%Y-%m-%d')}",
        f"- 语义阈值：{rag.settings.cache_semantic_threshold}（CACHE_SEMANTIC_THRESHOLD）",
        "- 探针：同义对应命中、易混对与跨库串扰不许命中；评测后缓存已清空",
        "",
        "| 类型 | 问法对 | 余弦 | 结果 | 耗时 |",
        "|---|---|---|---|---|",
        *rows,
        "",
    ]
    out = Path(__file__).resolve().parent.parent / "docs" / "eval" / "k6-cache.md"
    out.write_text("\n".join(report), encoding="utf-8")
    print(f"\n报告已写入 {out}")

    if failures:
        print("\n== 未通过 ==")
        for f in failures:
            print(" -", f)
        return 1
    print("\n全部探针通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
