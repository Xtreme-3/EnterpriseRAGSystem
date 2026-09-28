"""量出「库内问题的最低分」与「库外问题的最高分」，判断 similarity_threshold 还站不站得住。

**为什么需要这个脚本**：``similarity_threshold`` 不是拍脑袋定的常量，它必须落在
「库外噪声最高分」与「库内最弱一条命中」之间的空隙里 —— 低了压不干净噪声，
高了会把本来该召回的资料一起筛掉。这个空隙**取决于语料与 embedding 模型**，
所以每次改语料、加文档、换 embedding 供应商之后都必须重测。

本项目实测记录（每换一次语料都要在此追加一行）：

=================================  ===========  =================  ======
语料                                库外最高     库内 rank-5 最低    结论
=================================  ===========  =================  ======
kb_1 4 份 / 44 切片                 0.371        0.469              0.40 有效
kb_1 4 份 / 63 切片                 0.3750       0.5119             0.40 有效
kb_2 3 份 / 75 切片                 0.4726       0.4652             **重叠，见下**
=================================  ===========  =================  ======

**kb_2 为什么重叠、为什么仍然不改阈值**：库外最高分来自「美国站的退货窗口是多少天？」
（0.4726）—— 它问的是 kb_1 的业务制度，对 kb_2 是库外，但**同属电商企业制度这个语义域**，
跟人事财务文档共享大量词汇，所以向量分压不下去。而库内最弱是「试用期最长可以约定多久？」
（0.4652）。两者只差 0.0074。

- 调高阈值到 0.4726 以上 → 库内最弱那条一起被筛掉，那问必然召回失败；
- 调低 → 噪声更多。

实测（kb_2 评测）该噪声确实被召回进了 prompt，但 LLM 仍正确拒答，**拒答 3/3 全对**。
所以 0.40 在 kb_2 上的定位是「召回优先的粗筛」，判据不在它身上。真要收紧得改策略
（双阈值 / rerank），不是继续拧这个数 —— 见 docs/roadmap.md。

**方法**：把阈值临时置 0 取全部候选（否则被阈值筛掉的候选根本看不到），
对每个库内问题取向量路 top-5 里最低的那个原始余弦，对每个库外问题取最高的那个。

用法::

    python scripts/measure_similarity_margin.py            # 用量配置的 top_k
    python scripts/measure_similarity_margin.py --kb 1 --top-k 5
    python scripts/measure_similarity_margin.py --kb 2 --questions-file docs/eval/questions-kb2.json

**必须用与被测库匹配的问题集**：内置问题集只对应 kb_1。拿它去量 kb_2，
「库内问题」会全部量成噪声，结论完全不可信。

退出码 1 表示当前阈**不**落在空隙内，需要调整 ``app/config.py`` 的默认值。
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
from app.storage.vector_store import build_vector_store  # noqa: E402


def _k3_eval_module():
    """按文件路径加载 k3_eval（scripts 不是包，不能直接 import）。"""
    spec = importlib.util.spec_from_file_location("_k3_eval", ROOT / "scripts" / "k3_eval.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_eval_questions(path: str = ""):
    """复用 k3_eval 的问题集入口，避免两处问题集漂移。

    这条路径很关键：阈值必须落在**同一批问题**量出的空隙里。如果这里用一套问题、
    评测用另一套，调出来的阈值在评测上就不成立。
    """
    return _k3_eval_module().load_questions(path or None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="测 similarity_threshold 的安全空隙")
    parser.add_argument("--kb", type=int, default=1)
    parser.add_argument("--top-k", type=int, default=0, help="0 = 用配置")
    parser.add_argument(
        "--questions-file", default="", help="外部问题集 JSON；不传则用内置的 kb_1 基线 13 问"
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    top_k = args.top_k or settings.top_k
    configured = settings.similarity_threshold

    # 置 0 = 不筛，才能看到全部候选的真实分数
    settings = settings.model_copy(update={"similarity_threshold": 0.0})
    embedding = build_embedding(settings)
    store = build_vector_store(settings)

    in_kb: list[tuple[str, float]] = []
    out_kb: list[tuple[str, float]] = []

    for q in _load_eval_questions(args.questions_file):
        vector = embedding.embed([q.text])[0]
        hits = store.search(args.kb, vector, top_k)
        if not hits:
            continue
        if q.expect_docs:
            in_kb.append((q.text, min(h.score for h in hits)))
        else:
            out_kb.append((q.text, max(h.score for h in hits)))

    print(f"kb_{args.kb} | embedding={settings.embedding_model} | top_k={top_k} | 当前配置阈值={configured}")
    print(f"问题集: {args.questions_file or '内置基线（kb_1）'}\n")

    print(f"库外问题 {len(out_kb)} 条（最高分就是噪声上限）：")
    for text, score in sorted(out_kb, key=lambda x: -x[1]):
        print(f"  {score:.4f}  {text}")
    print(f"\n库内问题 {len(in_kb)} 条（最低分就是最弱一条命中）：")
    for text, score in sorted(in_kb, key=lambda x: x[1]):
        print(f"  {score:.4f}  {text}")

    noise = max((s for _, s in out_kb), default=0.0)
    weakest = min((s for _, s in in_kb), default=0.0)
    print(f"\n噪声上限 {noise:.4f}  <  安全间隙  <  库内最弱 {weakest:.4f}")

    if noise >= weakest:
        print("⚠️ 两条分布重叠，任何单一阈值都无法干净分离 —— 需要改检索策略，不是调阈值。")
        return 1
    if not noise < configured < weakest:
        print(f"⚠️ 当前阈值 {configured} 不在 ({noise:.4f}, {weakest:.4f}) 区间内，请调整 app/config.py。")
        return 1
    margin_lo = configured - noise
    margin_hi = weakest - configured
    print(f"✅ 当前阈值 {configured} 有效：距噪声上限 {margin_lo:+.4f}，距库内最弱 {margin_hi:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
