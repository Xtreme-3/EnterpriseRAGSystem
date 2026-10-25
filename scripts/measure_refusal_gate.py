"""量出 L1 双阈值拒答门在**生产配置**下对每个评测问题的实际判定 ——「逐问验证」的固化工具。

**为什么需要这个脚本（而不是只跑 k3_eval 看结果）**：门的触发条件是组合证据 ——
「向量候选 raw 余弦最高的一条 < ``answer_threshold``」**且**「关键词路证据不足」。
第一条可以直接算，但第二条**不能靠推断**：``tokenize`` 是中文重叠 2-gram，
「美国站的退货窗口是多少天？」会切出「美国」，只要库内**任何**切片含「美国」，
关键词路就非空。同理，库内问题若某个词没被任何切片覆盖，证据也可能意外不足 → 误杀。
这两种情况都只能实测。

**本脚本存在的原因本身是一条教训（2026-10-07）**：门的第一版把条件②写成
「关键词路 **0 命中**」（``not lexical_hits``），实测双库 6 条库外探针**全部漏网** ——
两库 29 问里有 4 条库外问题都在关键词路拿到了命中（「美国站的退货窗口…」命中
kb_2 的销售税切片），门从未触发过一次。**7 个离线单测全绿、469 项回归零红，
却零行为**。改成看**覆盖度**（``lexical_overlap``）后才真正分开。

本项目实测记录（每换语料 / 加文档 / 换分词都要在此追加一行）::

=================================  ===============  ===============  ========  ========
语料（问题集）                       向量max 库内最低  向量max 库外最高  词覆盖     门
                                                    （库内/库外）
=================================  ===============  ===============  ========  ========
kb_1 4 份 / 63 切片（13 问）          0.6580           0.3750*          2 / 1    无误杀
kb_2 3 份 / 75 切片（16 问）          0.7536           0.4727           2 / 1    无误杀
=================================  ===============  ===============  ========  ========

* kb_1 库外三问的向量 max 均低于粗筛线 0.40 → ``store.search`` 返回空 → 门不参与，
  走的是「检索为空」的老拒答路径（这就是为什么门只在 kb_2 这类噪声高于粗筛线的库上有效）。

**与 ``measure_similarity_margin.py`` 的分工**：那个脚本量的是「单一向量阈值能不能
分开两条分布」，用的是「库内 **rank-5 最低** vs 库外 **最高**」；本脚本量的是**门在生产
配置下到底拦谁、放谁**，用的是「库内/库外 **max**」。两个统计量不同，别互相引用数字 ——
门的判据是 max，不是 min。

输出两类问题：

===================  ==================================================================
**误杀**              库内问题（``expect_docs`` 非空）门触发 → 这问会白丢答案。**必须为 0。**
**漏网**              库外问题门未触发 → 拒答仍由 LLM 兜底（不算错误，但这次没省下调用）
===================  ==================================================================

用法::

    python scripts/measure_refusal_gate.py --kb 1
    python scripts/measure_refusal_gate.py --kb 2 --questions-file docs/eval/questions-kb2.json
    python scripts/measure_refusal_gate.py --kb 2 --show-lexical   # 打印漏网问命中的关键词

退出码 1 = 出现误杀（门限/策略需要调整，别上线）。
退出码 2 = 门的目标库外问（向量低于强相关线、且有候选进入门）**全部**漏网 → 门形同虚设。
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.providers.factory import build_embedding  # noqa: E402
from app.rag.hybrid import (  # noqa: E402
    LEXICAL_EVIDENCE_MIN_OVERLAP,
    lexical_overlap,
    tokenize,
)
from app.storage.vector_store import build_vector_store  # noqa: E402


def _load_questions(path: str = ""):
    """复用 k3_eval 的问题集入口 —— 门上线的判据必须与评测用同一批问题。"""
    spec = importlib.util.spec_from_file_location("_k3_eval", ROOT / "scripts" / "k3_eval.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.load_questions(path or None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="逐问验证 L1 双阈值门（生产配置口径）")
    parser.add_argument("--kb", type=int, default=1)
    parser.add_argument("--top-k", type=int, default=0, help="0 = 用配置")
    parser.add_argument("--questions-file", default="", help="外部问题集 JSON；不传则用内置 kb_1 基线")
    parser.add_argument("--show-lexical", action="store_true", help="漏网问题打印命中的关键词与切片片段")
    args = parser.parse_args(argv)

    settings = get_settings()
    top_k = args.top_k or settings.top_k
    gate = settings.answer_threshold

    # 生产配置口径：不覆写 similarity_threshold，store.search 会先按粗筛线筛一遍
    embedding = build_embedding(settings)
    store = build_vector_store(settings)

    print(
        f"kb_{args.kb} | embedding={settings.embedding_model} | top_k={top_k}\n"
        f"mode={settings.retrieval_mode} | 粗筛 similarity_threshold={settings.similarity_threshold} "
        f"| 强相关线 answer_threshold={gate}"
        + ("  ← 门已关闭，全部不触发" if gate <= 0 else "")
        + f"\n问题集: {args.questions_file or '内置基线（kb_1）'}\n"
    )

    rows: list[tuple[str, str, bool, float | None, int, int, bool, list[str]]] = []
    for q in _load_questions(args.questions_file):
        vector = embedding.embed([q.text])[0]
        vhits = store.search(args.kb, vector, top_k)
        lhits = store.search_lexical(args.kb, q.text, top_k)
        vmax = max((h.score for h in vhits), default=None)
        # 与 Retriever.retrieve_gated 完全同口径（同一函数、同一常量），避免口径漂移
        overlap = lexical_overlap(q.text, lhits)
        fired = (
            gate > 0
            and settings.retrieval_mode == "hybrid"
            and bool(vhits)
            and vmax is not None
            and vmax < gate
            and overlap < LEXICAL_EVIDENCE_MIN_OVERLAP
        )
        rows.append(
            (
                q.text,
                "库内" if q.expect_docs else "库外",
                fired,
                vmax,
                overlap,
                len(lhits),
                bool(q.expect_docs),
                [h.content[:60].replace("\n", " ") for h in lhits[:2]],
            )
        )

    print(f"{'类':<4}{'向量max':>9}{'词覆盖':>7}{'词条数':>7}  {'门':<5} 问题")
    print("-" * 96)
    for text, kind, fired, vmax, overlap, nlex, _, _ in rows:
        vs = f"{vmax:.4f}" if vmax is not None else "  ——  "
        mark = "触发" if fired else "—"
        print(f"{kind:<4}{vs:>9}{overlap:>7}{nlex:>7}  {mark:<5} {text}")

    misfire = [r for r in rows if r[6] and r[2]]   # 库内 + 触发
    missed = [r for r in rows if not r[6] and not r[2]]  # 库外 + 未触发
    in_kb = [r for r in rows if r[6]]
    out_kb = [r for r in rows if not r[6]]

    # 把两类的两个统计量摆出来，判断有没有「能用」的判别式（而不是靠推断）
    def _edge(rs, idx, fn):
        vals = [r[idx] for r in rs if r[idx] is not None]
        return f"{fn(vals):.4f}" if vals else "——"

    def _verdict(idx: int) -> str:
        lo, hi = _edge(in_kb, idx, min), _edge(out_kb, idx, max)
        if "——" in (lo, hi):
            return "无法判定"
        return "干净" if float(hi) < float(lo) else "重叠"

    print(
        f"\n向量 max：库内最低 {_edge(in_kb, 3, min)} | 库外最高 {_edge(out_kb, 3, max)} → {_verdict(3)}"
        f"\n词覆盖 ：库内最低 {_edge(in_kb, 4, min)} | 库外最高 {_edge(out_kb, 4, max)}"
        f" → {_verdict(4)}（门槛 {LEXICAL_EVIDENCE_MIN_OVERLAP}）"
    )

    print(
        f"\n库内 {len(in_kb)} 问：门触发 {len(misfire)}（**必须为 0**）"
        f"\n库外 {len(out_kb)} 问：门触发 {len(out_kb) - len(missed)}，漏网 {len(missed)}\n"
    )

    if misfire:
        print("❌ 误杀 —— 门会白丢库内答案，门限必须下调或放弃本块：")
        for text, _, _, vmax, _, nlex, _, _ in misfire:
            vs = f"{vmax:.4f}" if vmax is not None else "——"
            print(f"   [{vs} / 关键词 {nlex} 条] {text}")
        return 1

    # 只有「向量分够低、本该被门拦下」的库外问才算门的目标；向量低于粗筛线的那些
    # 根本到不了门（store.search 已返回空，走老的空检索拒答路径），不该算门失职。
    gateable = [r for r in out_kb if r[3] is not None and r[3] < gate]
    if gateable and all(r in missed for r in gateable):
        print(
            f"⚠️ 门的目标库外问（向量 < {gate} 且有候选）{len(gateable)} 条**全部漏网** ——"
            " 门在当前语料上形同虚设（拒答仍由 LLM 兜底，无回归）。"
        )
        return 2

    print("✅ 无误杀。")
    if missed:
        print(f"ℹ️ {len(missed)} 条库外漏网（关键词路非空）—— 门没省下这几次调用，但行为正确：")
        for text, _, _, vmax, _, nlex, _, snippets in missed:
            vs = f"{vmax:.4f}" if vmax is not None else "——"
            print(f"   [{vs} / 关键词 {nlex} 条] {text}")
            print(f"       query tokens: {'/'.join(tokenize(text))}")
            if args.show_lexical:
                for s in snippets:
                    print(f"       命中切片: {s}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
