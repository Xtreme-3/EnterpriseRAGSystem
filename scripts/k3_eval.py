"""K3 答案质量评测：固定问题集跑一遍完整 RAG，输出可比较的客观指标。

为什么要有这个脚本：K3 一批改了 3 处（来源元信息进 prompt、system 角色分离、
生成参数配置化），只靠"看答案感觉变好了"无法判断是哪一处起了作用、有没有改坏。
这里把评判标准固化成**机器可判定**的指标，同一问题集在新旧代码上各跑一次即可对比。

用法::

    python scripts/k3_eval.py --tag k3     --out docs/eval/k3-after.md
    python scripts/k3_eval.py --tag before --out docs/eval/k3-before.md

    # 指定知识库 / 检索模式 / 重排
    python scripts/k3_eval.py --kb 1 --mode hybrid --rerank false

    # 用外部问题集评另一个库（不动内置基线 13 问）
    python scripts/k3_eval.py --kb 2 --questions-file docs/eval/questions-kb2.json \\
        --tag kb2 --out docs/eval/kb2-baseline.md

指标（全部由程序判定，不靠人眼；`hit` 只在库内问题上计入分母）：

===============  ==========================================================
检索命中@1       期望文档出现在第 1 条来源
检索命中@3       期望文档出现在前 3 条来源
答案点名文档     答案正文里写到了期望文档的名字（prompt 注入来源头的直接效果）
正确拒答         库外问题回答「未找到相关信息」或没有任何来源命中
耗时             单问端到端毫秒数
===============  ==========================================================

**脚本必须对新旧两版代码都能跑**（它用来做 A/B），所以只依赖稳定的公开接口，
不引用 K3 新增的常量。正式评测按顺序跑两次并在同一份报告里对读。

**内置问题集只对应 kb_1**：它盯的是「4 份业务制度」这个库，换库跑必然全错。
评别的库要另写问题集文件并用 ``--questions-file`` 指过去 —— 这样老基线不会被动过，
新旧两次评测才有可比性。

问题集文件格式（``expect_docs`` 为空数组 = 库外问题，应拒答）::

    {
      "name": "kb2-hr-finance",
      "questions": [
        {"text": "员工年假有多少天？", "expect_docs": ["员工手册"], "note": "4.1 年假表"},
        {"text": "竞业限制最长多久？",  "expect_docs": ["员工手册"], "note": "6.2"},
        {"text": "库外问题占位",        "expect_docs": [],          "note": "库外"}
      ]
    }

顶层也可以直接是 ``questions`` 那个数组。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.rag.pipeline import RagPipeline  # noqa: E402
from app.storage.db import init_db  # noqa: E402

REFUSAL_MARK = "未找到相关信息"


@dataclass
class Q:
    """一条评测问题。``expect_docs`` 为空表示库外问题（应拒答）。"""

    text: str
    expect_docs: tuple[str, ...] = ()
    note: str = ""


QUESTIONS: list[Q] = [
    # ---- 库内：单文档定位 ----
    Q("供应商准入需要满足哪些基本资质？", ("供应商管理制度",), "资质表"),
    Q("皮具类的五金件有什么标准？", ("供应商管理制度",), "4.2 小节"),
    Q("美国站的退货窗口是多少天？", ("跨境售后政策",), "退换货窗口表"),
    Q("德国市场的退货规则有什么特殊之处？", ("跨境售后政策",), "德国特殊条款"),
    Q("发往欧盟的物流标准时效是多久？", ("跨境物流指南", "产品手册"), "时效表"),
    Q("保温杯的保温效能检测标准是什么？", ("产品手册", "供应商管理制度"), "质检要点"),
    Q("海关扣货的处理时效和方案是什么？", ("跨境物流指南",), "异常处理表"),
    Q("皮具出厂前要做哪些质检？", ("产品手册",), "2.3 质检标准"),
    # ---- 跨文档：答案需要来自两个不同文件 ----
    Q("物流破损导致客户投诉，责任和赔付怎么处理？", ("跨境物流指南", "跨境售后政策"), "跨文档"),
    Q("哪些情况会导致供应商合作被直接终止？", ("供应商管理制度",), "一票否决"),
    # ---- 库外：应拒答，考察会不会硬编 ----
    Q("出差住宿能报多少钱？", (), "库外·差旅"),
    Q("公司年假有几天？", (), "库外·年假"),
    Q("今天天气怎么样？", (), "库外·无关"),
]


def _question_from_dict(raw: object, where: str) -> Q:
    if not isinstance(raw, dict):
        raise SystemExit(f"{where}: 每条问题应是对象，实际是 {type(raw).__name__}")
    text = str(raw.get("text") or "").strip()
    if not text:
        raise SystemExit(f"{where}: 缺少 text 字段")
    docs = raw.get("expect_docs") or []
    if isinstance(docs, str):
        raise SystemExit(f"{where}: expect_docs 应为数组（如 [\"员工手册\"]），不是字符串")
    if not isinstance(docs, (list, tuple)):
        raise SystemExit(f"{where}: expect_docs 应为数组")
    return Q(
        text=text,
        expect_docs=tuple(str(d) for d in docs),
        note=str(raw.get("note") or ""),
    )


def load_questions(path: str | Path | None = None) -> list[Q]:
    """加载问题集。``path`` 为空（None 或空串）时返回内置的 kb_1 基线 13 问。

    这是全仓唯一的问题集入口：``measure_similarity_margin.py`` 也从这里取，
    避免两处各写一份、时间久了漂移。
    """
    if not path:
        return list(QUESTIONS)

    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"问题集文件不存在：{p}")
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{p} 不是合法 JSON：{exc}")

    rows = payload.get("questions") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        raise SystemExit(f"{p}：找不到非空的 questions 数组")

    prefix = payload.get("name", p.stem) if isinstance(payload, dict) else p.stem
    return [_question_from_dict(r, f"{p.name}·{prefix}#{i}") for i, r in enumerate(rows, 1)]


@dataclass
class Result:
    text: str
    note: str
    expect_docs: tuple[str, ...]
    answer: str
    refused: bool
    hit_top1: bool
    hit_top3: bool
    named: bool
    latency_ms: int
    sources: list[str] = field(default_factory=list)


def _first_docs(filenames: list[str], expect: tuple[str, ...], n: int) -> bool:
    return any(k in f for f in filenames[:n] for k in expect)


def run_one(rag: RagPipeline, kb_id: int, q: Q, top_k: int | None) -> Result:
    t0 = time.perf_counter()
    ans = rag.ask(kb_id, q.text, top_k=top_k)
    latency = int((time.perf_counter() - t0) * 1000)

    filenames = [s.filename for s in ans.sources]
    refused = REFUSAL_MARK in ans.answer or not ans.sources
    return Result(
        text=q.text,
        note=q.note,
        expect_docs=q.expect_docs,
        answer=ans.answer,
        refused=refused,
        hit_top1=bool(q.expect_docs) and _first_docs(filenames, q.expect_docs, 1),
        hit_top3=bool(q.expect_docs) and _first_docs(filenames, q.expect_docs, 3),
        named=bool(q.expect_docs) and any(k in ans.answer for k in q.expect_docs),
        latency_ms=latency,
        sources=[f"{f}#{s.chunk_index}" for f, s in zip(filenames, ans.sources)],
    )


def summarize(rs: list[Result]) -> dict[str, str]:
    in_kb = [r for r in rs if r.expect_docs]
    out_kb = [r for r in rs if not r.expect_docs]
    n = len(in_kb) or 1
    return {
        "库内问题数": str(len(in_kb)),
        "检索命中@1": f"{sum(r.hit_top1 for r in in_kb)}/{len(in_kb)}",
        "检索命中@3": f"{sum(r.hit_top3 for r in in_kb)}/{len(in_kb)}",
        "答案点名文档": f"{sum(r.named for r in in_kb)}/{len(in_kb)}",
        "命中率@1": f"{sum(r.hit_top1 for r in in_kb) / n:.0%}",
        "库外问题数": str(len(out_kb)),
        "正确拒答": f"{sum(r.refused for r in out_kb)}/{len(out_kb)}",
        "平均耗时(ms)": f"{sum(r.latency_ms for r in rs) // len(rs)}",
    }


def render_md(tag: str, settings_note: str, summary: dict[str, str], rs: list[Result]) -> str:
    lines = [f"# K3 答案质量评测 · {tag}", "", settings_note, "", "## 汇总", "", "| 指标 | 结果 |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in summary.items()]
    lines += ["", "## 逐题明细", "", "| # | 问题 | 期望文档 | 命中@1 | 命中@3 | 点名 | 拒答 | 耗时ms | 来源(top3) |",
              "|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rs, 1):
        lines.append(
            f"| {i} | {r.text} | {' / '.join(r.expect_docs) or '（库外）'} "
            f"| {'✅' if r.hit_top1 else ('❌' if r.expect_docs else '—')} "
            f"| {'✅' if r.hit_top3 else ('❌' if r.expect_docs else '—')} "
            f"| {'✅' if r.named else ('❌' if r.expect_docs else '—')} "
            f"| {'✅' if r.refused else ('·' if r.expect_docs else '❌')} "
            f"| {r.latency_ms} | {', '.join(r.sources[:3])} |"
        )
    lines += ["", "## 答案原文（截断 300 字）", ""]
    for i, r in enumerate(rs, 1):
        body = r.answer.replace("\n", " ").strip()
        lines.append(f"**{i}. {r.text}**（{r.note}）")
        lines.append(f"> {body[:300]}{'…' if len(body) > 300 else ''}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", type=int, default=1, help="知识库 ID")
    ap.add_argument("--tag", default="run", help="本次运行的标签（写进报告标题）")
    ap.add_argument("--out", default="", help="markdown 报告输出路径")
    ap.add_argument("--json", default="", help="原始结果 JSON 输出路径（便于逐题 diff）")
    ap.add_argument("--top-k", type=int, default=0, help="覆盖 top_k（0 = 用配置）")
    ap.add_argument("--mode", default="", help="覆盖 retrieval_mode：vector | hybrid")
    ap.add_argument("--rerank", default="", help="覆盖 rerank：true | false")
    ap.add_argument(
        "--questions-file",
        default="",
        help="外部问题集 JSON；不传则用内置的 kb_1 基线 13 问",
    )
    args = ap.parse_args()

    questions = load_questions(args.questions_file)
    src_label = args.questions_file or "内置基线（kb_1）"

    settings = get_settings()
    if args.mode:
        settings.retrieval_mode = args.mode
    if args.rerank:
        settings.rerank = args.rerank.lower() == "true"

    _, session_factory = init_db(settings)
    rag = RagPipeline(settings=settings, session_factory=session_factory)

    note = (
        f"- 知识库: kb_{args.kb} | 供应商: rag={settings.rag_provider} "
        f"llm={settings.llm_provider or settings.rag_provider}({settings.llm_model}) "
        f"embedding={settings.embedding_provider}({settings.embedding_model})\n"
        f"- 检索: mode={settings.retrieval_mode} top_k={args.top_k or settings.top_k} "
        f"threshold={settings.similarity_threshold} rerank={settings.rerank}\n"
        f"- 切块: strategy={settings.chunk_strategy} size={settings.chunk_size}\n"
        f"- 问题集: {src_label}（{len(questions)} 问）"
    )
    print(note)

    rs = [run_one(rag, args.kb, q, args.top_k or None) for q in questions]
    summary = summarize(rs)

    print("\n== 汇总 ==")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    md = render_md(args.tag, note, summary, rs)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md, encoding="utf-8")
        print(f"\n报告已写入 {p}")
    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "tag": args.tag,
                    "kb": args.kb,
                    "questions_source": src_label,
                    "summary": summary,
                    "results": [asdict(r) for r in rs],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"原始结果已写入 {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
