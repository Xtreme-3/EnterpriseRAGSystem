"""L1 双阈值拒答门：向量候选全弱 + 关键词证据不足 → 不调 LLM 直接标准拒答。

注意「关键词证据」判的是**覆盖了多少查询词**（``lexical_overlap``），不是「有没有命中」
—— 中文重叠 2-gram 会让库外问题也拿到命中，写成「0 命中」门就永不触发（实测过）。

全部离线：stub 向量库 + mock embedding + 记录式 LLM，不依赖 chroma / PG / 真实 Key。
门语义与护栏值依据见 `docs/requirements/L1-dual-threshold-refusal.md` 与
`app/config.py` 的 `answer_threshold` 注释。
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.providers.mock import MockEmbedding
from app.rag.generator import NO_HIT_ANSWER
from app.rag.hybrid import LEXICAL_EVIDENCE_MIN_OVERLAP, lexical_overlap
from app.rag.pipeline import RagPipeline
from app.storage.db import init_db
from app.storage.vector_store import ScoredChunk, VectorStore


class _RecordingLLM:
    """记录 complete/stream 调用；门生效的判据就是这里零调用。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete(self, prompt: str, *, system: str | None = None, model: str | None = None, **_: object) -> str:
        self.calls.append({"prompt": prompt})
        return "答案"

    def stream(self, prompt: str, *, system: str | None = None, model: str | None = None, **_: object) -> Iterator[str]:
        self.calls.append({"prompt": prompt})
        yield "答案"


class _GateStore(VectorStore):
    """向量路与关键词路命中分开配置，用于单边控制门的两条证据。"""

    def __init__(
        self,
        vector_hits: list[ScoredChunk],
        lexical_hits: list[ScoredChunk] | None = None,
    ) -> None:
        self.vector_hits = vector_hits
        self.lexical_hits = lexical_hits or []

    def ensure_collection(self, kb_id: int, dim: int) -> None:
        pass

    def add(self, kb_id: int, chunks: list) -> None:
        pass

    def search(self, kb_id: int, vector: list[float], top_k: int) -> list[ScoredChunk]:
        return list(self.vector_hits[:top_k])

    def search_lexical(self, kb_id: int, query: str, top_k: int) -> list[ScoredChunk]:
        return list(self.lexical_hits[:top_k])

    def delete_document(self, kb_id: int, document_id: int) -> None:
        pass

    def delete_collection(self, kb_id: int) -> None:
        pass

    def document_counts(self, kb_id: int) -> dict[int, int]:
        return {}


def _hit(doc_id: int, score: float, content: str = "弱相关噪声切片") -> ScoredChunk:
    return ScoredChunk(
        document_id=doc_id, kb_id=1, chunk_index=0, content=content, score=score
    )


def _rag(
    tmp_path: Path,
    store: _GateStore,
    llm: _RecordingLLM,
    *,
    retrieval_mode: str = "hybrid",
    answer_threshold: float = 0.48,
) -> RagPipeline:
    settings = Settings(
        rag_provider="mock",
        data_dir=tmp_path / "data",
        top_k=3,
        retrieval_mode=retrieval_mode,
        vector_store="chroma",
        answer_threshold=answer_threshold,
    )
    _, session_factory = init_db(settings)
    return RagPipeline(
        settings=settings,
        session_factory=session_factory,
        vector_store=store,
        embedding=MockEmbedding(),
        llm=llm,
    )


def test_gate_fires_skips_llm_and_answers_no_hit(tmp_path: Path) -> None:
    """向量分全低于强相关线且关键词 0 命中 → 拒答、零 LLM 调用、无来源。"""
    llm = _RecordingLLM()
    store = _GateStore(vector_hits=[_hit(1, 0.45)])  # 0.40 ≤ 0.45 < 0.48
    rag = _rag(tmp_path, store, llm)

    ans = rag.ask(1, "皮具类五金件电镀厚度是多少？")

    assert llm.calls == []
    assert ans.answer == NO_HIT_ANSWER
    assert ans.sources == []


def test_gate_holds_when_lexical_evidence_is_strong(tmp_path: Path) -> None:
    """关键词路覆盖 ≥2 个查询词 → 有实质证据，门不触发（库内最弱问靠它保命）。"""
    llm = _RecordingLLM()
    store = _GateStore(
        vector_hits=[_hit(1, 0.45)],
        lexical_hits=[_hit(1, 0.8, content="试用期最长可以约定六个月。")],
    )
    rag = _rag(tmp_path, store, llm)

    ans = rag.ask(1, "试用期最长可以约定多久？")

    assert len(llm.calls) == 1
    assert ans.answer == "答案"
    assert len(ans.sources) == 1


def test_gate_fires_when_lexical_evidence_is_weak(tmp_path: Path) -> None:
    """**本门的核心回归**：关键词路非空但只覆盖 1 个查询词 = 证据不足，门照样触发。

    这是第一版把条件写成「关键词 0 命中」时的漏洞 —— 中文重叠 2-gram 让库外问题
    也能拿到命中（「美国站的退货窗口…」命中库内含有「美国」的切片），
    只要写成 ``not lexical_hits`` 门就永不触发（实测双库 6 条库外探针全漏网）。
    """
    llm = _RecordingLLM()
    store = _GateStore(
        vector_hits=[_hit(1, 0.4727)],  # 强相关线之下
        lexical_hits=[_hit(1, 1.0, content="达到美国哪些条件就要注册并申报销售税？")],
    )
    rag = _rag(tmp_path, store, llm)

    ans = rag.ask(1, "美国站的退货窗口是多少天？")

    assert llm.calls == []
    assert ans.answer == NO_HIT_ANSWER
    assert ans.sources == []


def test_gate_holds_when_any_vector_score_above_threshold(tmp_path: Path) -> None:
    """只要有一条向量候选 ≥ 强相关线，门就不触发（强相关即正证据）。"""
    llm = _RecordingLLM()
    store = _GateStore(vector_hits=[_hit(1, 0.45), _hit(2, 0.9, content="强相关支撑")])
    rag = _rag(tmp_path, store, llm)

    ans = rag.ask(1, "供应商准入条件？")

    assert len(llm.calls) == 1
    assert ans.answer == "答案"
    assert len(ans.sources) == 2


def test_lexical_overlap_counts_only_query_terms() -> None:
    """``lexical_overlap`` 只数查询词，且跨存储可比：不看 score，只看正文覆盖。

    用同一批 ScoredChunk 但**分数量纲不同**（0.06 仿 pgvector ts_rank / 3.0 仿 chroma
    命中词数）验证结论一致 —— 这正是不能用 score 当门槛的原因。
    """
    query = "美国站的退货窗口是多少天？"
    weak = [_hit(1, 0.06, content="达到美国哪些条件就要注册并申报销售税？")]
    strong = [_hit(2, 3.0, content="退货窗口：美国站 30 天。")]
    assert lexical_overlap(query, []) == 0
    assert lexical_overlap(query, weak) == 1
    assert lexical_overlap(query, strong) >= LEXICAL_EVIDENCE_MIN_OVERLAP


def test_lexical_overlap_zero_on_empty_token_query() -> None:
    """无可提取词（空串/单字符）→ 0，不会被误判成「证据充足」。"""
    assert lexical_overlap("？", [_hit(1, 5.0, content="任何内容")]) == 0


def test_gate_off_in_vector_mode(tmp_path: Path) -> None:
    """vector 模式没有关键词路证据，门不启用（L1 定案：防止误杀库内最弱问）。"""
    llm = _RecordingLLM()
    store = _GateStore(vector_hits=[_hit(1, 0.45)])
    rag = _rag(tmp_path, store, llm, retrieval_mode="vector")

    ans = rag.ask(1, "任何问题？")

    assert len(llm.calls) == 1
    assert ans.answer == "答案"


def test_gate_disabled_when_threshold_zero(tmp_path: Path) -> None:
    """answer_threshold=0 = 门关闭，行为完全回退到「拒答全靠模型」。"""
    llm = _RecordingLLM()
    store = _GateStore(vector_hits=[_hit(1, 0.45)])
    rag = _rag(tmp_path, store, llm, answer_threshold=0)

    ans = rag.ask(1, "任何问题？")

    assert len(llm.calls) == 1
    assert ans.answer == "答案"


def test_stream_gate_fires(tmp_path: Path) -> None:
    """流式路径同门：sources 事件在前且为空、答案 = 标准拒答、零 LLM 调用。"""
    llm = _RecordingLLM()
    store = _GateStore(vector_hits=[_hit(1, 0.45)])
    rag = _rag(tmp_path, store, llm)

    events = list(rag.ask_stream(1, "皮具类五金件电镀厚度是多少？"))

    types = [e["type"] for e in events]
    assert types[0] == "stage"
    assert "sources" in types
    assert types.index("sources") < types.index("token")
    sources_event = next(e for e in events if e["type"] == "sources")
    assert sources_event["sources"] == []
    done = events[-1]
    assert done["type"] == "done"
    assert done["answer"] == NO_HIT_ANSWER
    assert llm.calls == []


def test_default_answer_threshold_in_gap_between_out_kb_and_in_kb_max() -> None:
    """默认强相关线必须落在实测间隙 (kb_2 库外 max, 库内最低的 max) 之内。

    实测（2026-10-07，`scripts/measure_refusal_gate.py`，生产配置口径，
    与 `similarity_threshold` 同一批问题集）。**门用的统计量是各问的 max**，
    不是 `measure_similarity_margin.py` 的「库内 rank-5 最低」—— 两者别混：

    - kb_1（4 份 / 63 切片，13 问）：库外 max 最高 **0.3750**（且低于粗筛线 0.40 →
      门不参与，走空检索拒答），库内 max 最低 **0.6580**
    - kb_2（3 份 / 75 切片，16 问）：库外 max 最高 **0.4727**，库内 max 最低 **0.7536**

    取 (0.4727, 0.6580) 内：kb_2 的库外探针被门拦下，两库库内一条不伤。
    下界是紧的一侧（余量 0.0073），上界余量 0.178 —— 未来语料变差时先破下界。

    ⚠️ 边界值是**本演示语料 + 当前 embedding** 的实测结果，换语料 / 加文档 /
    换 embedding 后必须随 `scripts/measure_refusal_gate.py` 重测（退出码 1 = 误杀）。
    """
    t = Settings(_env_file=None).answer_threshold
    assert t > 0.4727, f"门限 {t} 落在 kb_2 库外噪声带内（实测最高 0.4727），门形同虚设"
    assert t < 0.6580, f"门限 {t} 会误杀 kb_1 库内最弱问（实测 max 最低 0.6580）"


def test_lexical_evidence_threshold_separates_measured_overlap() -> None:
    """关键词证据门槛必须卡在实测的 (库外最高 1, 库内最低 2) 之间。

    门槛若 ≤1 → 库外探针那 1 个词的碰巧命中就被当成「有证据」，门永不触发
    （第一版写成「0 命中」就是这个后果的极端版：门槛实际上是 1）。
    门槛若 >2 → 库内问题会被误判为「无证据」，配合弱向量分就误杀。
    """
    floor_ = 1  # 库外问题最多覆盖的查询词数（实测）
    ceiling = 2  # 库内问题最少覆盖的查询词数（实测）
    assert floor_ < LEXICAL_EVIDENCE_MIN_OVERLAP <= ceiling, (
        f"门槛 {LEXICAL_EVIDENCE_MIN_OVERLAP} 不在实测区间 ({floor_}, {ceiling}] 内："
        "过小则门形同虚设，过大则误杀库内问题"
    )
