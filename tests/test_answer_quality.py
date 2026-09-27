"""K3 答案质量：来源元信息进 prompt、system 角色分离、生成参数配置化。

三层，全部**离线可跑**（mock 供应商 + 临时目录，不依赖 PostgreSQL / 真实 Key）：

1. 生成层：``Generator.build_prompt`` 把「文件名 + 块号」渲染成每块资料的来源头，
   让模型能写出「根据《供应商管理制度》第 4.2 条…」而不是笼统的"资料显示"；
   拿不到文件名时退回旧的「[N] 正文」格式（编号不丢，只是少一层元信息）。
2. 供应商层：``LLMProvider`` 新增 ``system`` 槽位。系统规则走独立 system 消息
   （真模型对 system 的遵循度显著高于塞进 user 段）；传 ``None`` 退化为单条 user，
   与改动前完全一致。``max_tokens`` 改为可由配置注入的默认上限。
3. 编排层：``RagPipeline`` 必须在**生成之前**解析文档名，否则 prompt 里只有编号。

``max_tokens`` 语义：``None`` = 用实现自身的默认上限（由 ``llm_max_tokens`` 注入）。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.core.models import Document, KnowledgeBase, User
from app.providers.base import LLMProvider
from app.providers.mock import MockEmbedding, MockLLM
from app.providers.openai_compat import OpenAICompatLLM
from app.rag.generator import SYSTEM_PROMPT, Generator, citation_issues
from app.rag.pipeline import RagPipeline
from app.storage.db import get_db, init_db
from app.storage.vector_store import ChunkToIndex, ScoredChunk, VectorStore

# ---------------------------------------------------------------- 测试替身


class _RecordingLLM(LLMProvider):
    """记录每次调用的 prompt / system / max_tokens。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete(
        self, prompt: str, *, max_tokens: int | None = None, system: str | None = None
    ) -> str:
        self.calls.append({"prompt": prompt, "system": system, "max_tokens": max_tokens})
        return "答案"

    def stream(
        self, prompt: str, *, max_tokens: int | None = None, system: str | None = None
    ) -> Iterator[str]:
        self.calls.append({"prompt": prompt, "system": system, "max_tokens": max_tokens})
        yield "答案"


class _StubStore(VectorStore):
    """固定召回，避免测试依赖 chroma / pgvector。"""

    def __init__(self, hits: list[ScoredChunk]) -> None:
        self.hits = hits

    def ensure_collection(self, kb_id: int, dim: int) -> None:
        pass

    def add(self, kb_id: int, chunks: list[ChunkToIndex]) -> None:
        pass

    def search(self, kb_id: int, vector: list[float], top_k: int) -> list[ScoredChunk]:
        return list(self.hits[:top_k])

    def search_lexical(self, kb_id: int, query: str, top_k: int) -> list[ScoredChunk]:
        return []

    def delete_document(self, kb_id: int, document_id: int) -> None:
        pass

    def delete_collection(self, kb_id: int) -> None:
        pass

    def document_counts(self, kb_id: int) -> dict[int, int]:
        return {}


def _chunks(*items: tuple[int, int, str]) -> list[ScoredChunk]:
    """(document_id, chunk_index, content) → ScoredChunk 列表。"""
    return [
        ScoredChunk(document_id=doc_id, kb_id=1, chunk_index=idx, content=content, score=0.9)
        for doc_id, idx, content in items
    ]


def _stub_openai_llm(captured: list[dict], *, stream: bool = False) -> OpenAICompatLLM:
    """把 OpenAICompatLLM 的客户端换成记录参数的假客户端。"""
    if stream:
        chunk = SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="答"))]
        )
        resp: object = iter([chunk])
    else:
        resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="完整答案"))]
        )
    llm = OpenAICompatLLM(base_url="http://stub/v1", api_key="k", model="m")
    llm._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: (captured.append(kw), resp)[1])
        )
    )
    return llm


# ---------------------------------------------------------------- 1. 生成层：来源头


def test_build_prompt_renders_source_header_with_filename_and_chunk() -> None:
    """每块资料带出「[N]（来源：文件名 · 第 M 块）」，块号对人类是 1-based。"""
    g = Generator(_RecordingLLM())
    chunks = _chunks(
        (7, 2, "供应商须提供营业执照、税务登记证。"),
        (9, 0, "五金件执行 GB/T 标准。"),
    )
    prompt = g.build_prompt(
        "供应商准入条件？", chunks, filenames={7: "供应商管理制度.pdf", 9: "产品手册.pdf"}
    )
    assert "[1]（来源：供应商管理制度.pdf · 第 3 块）" in prompt
    assert "[2]（来源：产品手册.pdf · 第 1 块）" in prompt
    # 正文紧随来源头，没有被吃掉
    assert "供应商须提供营业执照" in prompt
    assert "五金件执行 GB/T 标准" in prompt


def test_build_prompt_falls_back_when_filename_unknown() -> None:
    """拿不到文件名（未提供映射 / 文档已删）时退回「[N] 正文」，编号不丢。"""
    g = Generator(_RecordingLLM())
    chunks = _chunks((7, 0, "差旅报销制度说明"))

    # 未提供映射：直接调用 Generator 的场景（如单测、无元数据库）
    assert "[1] 差旅报销制度说明" in g.build_prompt("q", chunks)

    # 映射里没有这个文档
    p = g.build_prompt("q", chunks, filenames={99: "别的.pdf"})
    assert "[1] 差旅报销制度说明" in p
    assert "别的.pdf" not in p


def test_build_prompt_no_history_omits_history_block_with_filenames() -> None:
    """J1 回归：无历史时不出现【对话历史】块（带文件名也不改变这一点）。"""
    g = Generator(_RecordingLLM())
    p = g.build_prompt(
        "供应商准入条件？", _chunks((1, 0, "需营业执照")), filenames={1: "a.pdf"}
    )
    assert "【对话历史】" not in p
    assert p.index("【资料】") < p.index("【用户问题】")


# ---------------------------------------------------------------- 2. 生成层：system 分离


def test_generate_sends_system_prompt_in_separate_slot() -> None:
    """系统规则走 system 槽位，user 段只留「资料 + 问题」。"""
    llm = _RecordingLLM()
    Generator(llm).generate("供应商准入条件？", _chunks((1, 0, "需营业执照")))
    assert llm.calls[0]["system"] == SYSTEM_PROMPT
    assert "你是企业内部知识库问答助手" not in llm.calls[0]["prompt"]
    assert "【资料】" in llm.calls[0]["prompt"]
    assert "【用户问题】" in llm.calls[0]["prompt"]


def test_stream_sends_system_prompt_too() -> None:
    """流式与非流式必须下发同一个 system（两处漂移会让答案风格不一致）。"""
    llm = _RecordingLLM()
    list(Generator(llm).stream("q", _chunks((1, 0, "需营业执照"))))
    assert llm.calls[0]["system"] == SYSTEM_PROMPT


def test_empty_hits_still_skip_llm() -> None:
    """检索为空仍是本地固定文案，不消耗模型调用（K1 行为不变）。"""
    llm = _RecordingLLM()
    assert Generator(llm).generate("q", []) == "资料库中未找到相关信息。"
    assert llm.calls == []


# ---------------------------------------------------------------- 3. 供应商层：OpenAI 兼容


def test_openai_compat_puts_system_as_first_message() -> None:
    captured: list[dict] = []
    _stub_openai_llm(captured).complete("问题", system="规则")
    msgs = captured[0]["messages"]
    assert msgs[0] == {"role": "system", "content": "规则"}
    assert msgs[1] == {"role": "user", "content": "问题"}
    assert len(msgs) == 2


def test_openai_compat_without_system_degrades_to_single_user_message() -> None:
    """传 None 时与改动前一致：只有一条 user 消息（回归安全）。"""
    captured: list[dict] = []
    _stub_openai_llm(captured).complete("问题")
    msgs = captured[0]["messages"]
    assert len(msgs) == 1
    assert msgs[0] == {"role": "user", "content": "问题"}


def test_openai_compat_stream_also_carries_system() -> None:
    captured: list[dict] = []
    out = list(_stub_openai_llm(captured, stream=True).stream("问题", system="规则"))
    assert out == ["答"]
    assert captured[0]["messages"][0] == {"role": "system", "content": "规则"}
    assert captured[0]["stream"] is True


def test_openai_compat_uses_configured_generation_defaults() -> None:
    """temperature / max_tokens 由配置注入，不再硬编码在 _params 里。"""
    captured: list[dict] = []
    llm = OpenAICompatLLM(
        base_url="http://stub/v1", api_key="k", model="m", temperature=0.7, max_tokens=512
    )
    resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="x"))])
    llm._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: (captured.append(kw), resp)[1])
        )
    )
    llm.complete("问题")
    assert captured[0]["temperature"] == 0.7
    assert captured[0]["max_tokens"] == 512

    # 调用级显式传值仍然优先（query_rewriter 用 128 限制改写长度）
    llm.complete("问题", max_tokens=64)
    assert captured[1]["max_tokens"] == 64


def test_settings_expose_generation_params() -> None:
    s = Settings(_env_file=None)
    assert s.llm_temperature == 0.2
    # 推理模型（qwen3.8-flash 等）的 reasoning_tokens 与正文**共用** max_tokens 预算。
    # 实测 1024 时 reasoning 吃掉 922 个 token，正文只剩 ~100 → finish_reason=length、
    # 答案被截在句子中间甚至为空。默认必须留够正文空间。
    assert s.llm_max_tokens >= 2048


# ---------------------------------------------------------------- 3b. 截断与空答案防护


def test_system_prompt_requires_naming_the_source_document() -> None:
    """只要求标 [N] 的话，模型就只标编号、永远不会写出文档名（实测点名率 1/10）。"""
    assert "文档名" in SYSTEM_PROMPT
    assert "章节" in SYSTEM_PROMPT


def test_openai_compat_warns_when_answer_truncated_by_token_limit(caplog) -> None:
    """finish_reason=length 意味着答案被砍断——必须留下告警，不能静默交付半句答案。"""
    captured: list[dict] = []
    resp = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content="结论：物流破损按 P2 处理，"),
            finish_reason="length",
        )]
    )
    llm = OpenAICompatLLM(base_url="http://stub/v1", api_key="k", model="m")
    llm._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: (captured.append(kw), resp)[1])
        )
    )

    with caplog.at_level("WARNING"):
        out = llm.complete("问题")

    assert out == "结论：物流破损按 P2 处理，"  # 已有内容照常返回，不吞
    assert any("length" in r.message or "截断" in r.message for r in caplog.records)


def test_generate_returns_diagnostic_instead_of_blank_answer() -> None:
    """模型返回空串时不能把空答案丢给前端（表现为一个空气泡）。"""
    from app.rag.generator import LLM_EMPTY_ANSWER

    class _BlankLLM(_RecordingLLM):
        def complete(self, prompt, *, max_tokens=None, system=None) -> str:  # type: ignore[override]
            self.calls.append({"prompt": prompt, "system": system, "max_tokens": max_tokens})
            return "   "

    assert Generator(_BlankLLM()).generate("q", _chunks((1, 0, "x"))) == LLM_EMPTY_ANSWER


def test_stream_returns_diagnostic_instead_of_blank_answer() -> None:
    """流式同理：一个 token 都没产出时给出可读提示，而不是让前端空转。"""
    from app.rag.generator import LLM_EMPTY_ANSWER

    class _BlankStreamLLM(_RecordingLLM):
        def stream(self, prompt, *, max_tokens=None, system=None):  # type: ignore[override]
            self.calls.append({"prompt": prompt, "system": system, "max_tokens": max_tokens})
            yield from ()

    assert list(Generator(_BlankStreamLLM()).stream("q", _chunks((1, 0, "x")))) == [LLM_EMPTY_ANSWER]


def test_stream_diagnostic_does_not_swallow_mid_stream_error() -> None:
    """中途异常必须照原样抛出——只有"一个 token 都没产出"才降级成提示语。"""
    from tests.test_streaming import _StubLLM

    llm = _StubLLM(tokens=["答案", "断"], boom_at=1)
    with pytest.raises(RuntimeError, match="模型连接中断"):
        list(Generator(llm).stream("q", _chunks((1, 0, "x"))))


# ---------------------------------------------------------------- 3c. 推理开关（enable_thinking）


def test_thinking_flag_defaults_to_unset() -> None:
    """默认不下发该字段——中转站/其它供应商可能不认识它（实测多发字段会 400）。"""
    assert Settings(_env_file=None).llm_enable_thinking == ""


def test_openai_compat_omits_thinking_field_by_default() -> None:
    captured: list[dict] = []
    _stub_openai_llm(captured).complete("问题")
    assert "extra_body" not in captured[0]


def test_openai_compat_passes_enable_thinking_when_configured() -> None:
    """显式关闭思考：Qwen3 系推理模型的 reasoning token 与正文共用预算，
    实测同一问题 开思考 17.9s / 关思考 6.3s，且关思考后正文更完整。"""
    captured: list[dict] = []
    llm = OpenAICompatLLM(
        base_url="http://stub/v1", api_key="k", model="m", enable_thinking=False
    )
    resp = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="x"))])
    llm._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: (captured.append(kw), resp)[1])
        )
    )
    llm.complete("问题")
    assert captured[0]["extra_body"] == {"enable_thinking": False}


def test_build_llm_maps_thinking_setting(monkeypatch) -> None:
    """配置字符串 → 三态：''（不下发）/ True / False。"""
    from app.providers import openai_compat
    from app.providers.factory import build_llm

    monkeypatch.setattr(
        openai_compat,
        "_make_client",
        lambda **kw: SimpleNamespace(base_url=kw["base_url"], api_key=kw["api_key"]),
    )
    base = {
        "_env_file": None,
        "rag_provider": "dashscope",
        "dashscope_api_key": "sk-relay",
        "dashscope_base_url": "https://relay.example/v1",
    }
    assert build_llm(Settings(**base, llm_enable_thinking=""))._enable_thinking is None
    assert build_llm(Settings(**base, llm_enable_thinking="false"))._enable_thinking is False
    assert build_llm(Settings(**base, llm_enable_thinking="TRUE"))._enable_thinking is True


def test_thinking_setting_rejects_unknown_value() -> None:
    with pytest.raises(Exception):
        Settings(_env_file=None, llm_enable_thinking="maybe")


# ---------------------------------------------------------------- 4. Mock 供应商兼容


def test_mock_llm_accepts_and_ignores_system() -> None:
    """MockLLM 忽略 system，但签名必须能吃下它，否则全链路在 mock 下直接 TypeError。"""
    llm = MockLLM()
    prompt = "【资料】\n[1] 出差住宿标准：一线城市每晚不超过六百元。\n\n【用户问题】\n住宿标准？\n"
    assert llm.complete(prompt, system=SYSTEM_PROMPT) == llm.complete(prompt)


def test_mock_llm_parses_prompt_with_source_headers() -> None:
    """来源头不能污染 MockLLM 的块解析（否则「（来源：…第 1 块）」会被当成事实句）。"""
    g = Generator(MockLLM())
    prompt = g.build_prompt(
        "供应商准入条件？",
        _chunks((1, 0, "供应商须提供营业执照与近三年纳税证明。")),
        filenames={1: "供应商管理制度.pdf"},
    )
    query, blocks = MockLLM._parse_prompt(prompt)
    assert query == "供应商准入条件？"
    assert len(blocks) == 1
    assert "营业执照" in blocks[0]
    assert "（来源：" not in blocks[0]


# ---------------------------------------------------------------- 5. 编排层：生成前解析文件名


@pytest.fixture()
def pipeline_env(tmp_path: Path) -> tuple[Settings, sessionmaker, int, int]:
    """隔离环境：mock 供应商 + 临时 data 目录 + 一个已索引文档。"""
    settings = Settings(
        rag_provider="mock",
        data_dir=tmp_path / "data",
        top_k=2,
        retrieval_mode="vector",
        vector_store="chroma",
    )
    _, session_factory = init_db(settings)
    # 用 get_db（提交）而不是裸 Session（关闭即回滚）——RagPipeline 会开新会话查文件名
    with get_db(session_factory) as db:
        user = User(username="_k3_pipeline", hashed_password="")
        db.add(user)
        db.flush()
        kb = KnowledgeBase(name="供应商库", description="", user_id=user.id)
        db.add(kb)
        db.flush()
        doc = Document(
            kb_id=kb.id,
            filename="供应商管理制度.pdf",
            file_type="pdf",
            status="indexed",
            chunk_count=1,
        )
        db.add(doc)
        db.flush()
        kb_id, doc_id = kb.id, doc.id
    return settings, session_factory, kb_id, doc_id


def test_pipeline_prompt_carries_real_filename(
    pipeline_env: tuple[Settings, sessionmaker, int, int],
) -> None:
    """文档名必须在**生成之前**解析好并写进 prompt，否则模型无从引用具体文档。"""
    settings, session_factory, kb_id, doc_id = pipeline_env
    llm = _RecordingLLM()
    store = _StubStore(
        [
            ScoredChunk(
                document_id=doc_id,
                kb_id=kb_id,
                chunk_index=2,
                content="供应商须提供营业执照。",
                score=0.9,
            )
        ]
    )
    rag = RagPipeline(
        settings=settings,
        session_factory=session_factory,
        vector_store=store,
        embedding=MockEmbedding(),
        llm=llm,
    )

    ans = rag.ask(kb_id, "供应商准入条件？")

    assert "供应商管理制度.pdf" in llm.calls[0]["prompt"]
    assert "第 3 块" in llm.calls[0]["prompt"]
    assert llm.calls[0]["system"] == SYSTEM_PROMPT
    assert ans.sources[0].filename == "供应商管理制度.pdf"


def test_pipeline_stream_prompt_carries_real_filename(
    pipeline_env: tuple[Settings, sessionmaker, int, int],
) -> None:
    """流式路径与 ask() 用同一份文档名映射，不能只在非流式下生效。"""
    settings, session_factory, kb_id, doc_id = pipeline_env
    llm = _RecordingLLM()
    store = _StubStore(
        [
            ScoredChunk(
                document_id=doc_id,
                kb_id=kb_id,
                chunk_index=0,
                content="供应商须提供营业执照。",
                score=0.9,
            )
        ]
    )
    rag = RagPipeline(
        settings=settings,
        session_factory=session_factory,
        vector_store=store,
        embedding=MockEmbedding(),
        llm=llm,
    )

    events = list(rag.ask_stream(kb_id, "供应商准入条件？"))

    assert "供应商管理制度.pdf" in llm.calls[0]["prompt"]
    done = [e for e in events if e["type"] == "done"][0]
    assert done["sources"][0].filename == "供应商管理制度.pdf"


# ---------------------------------------------------------------- 6. 引用编号校验（批 2-1）


def test_citation_issues_flags_out_of_range_number() -> None:
    """引用了不存在的编号（只有 5 块却写了 [9]）→ 必须报出来，这是本功能的唯一目的。"""
    answer = "根据《供应商管理制度》[1]，须提供营业执照。另见《产品手册》[9]。"
    assert citation_issues(answer, 5) == [9]


def test_citation_issues_accepts_full_valid_range() -> None:
    """1..N 全部合法；N 本身是合法上界（编号 1-based，不能把 [5]/5 块误判为越界）。"""
    assert citation_issues("见 [1][2][3][4][5]", 5) == []


def test_citation_issues_treats_zero_as_invalid() -> None:
    """[0] 是非法编号（编号从 1 起），必须报出来而不是当合法值放过。"""
    assert citation_issues("见 [0]", 5) == [0]


def test_citation_issues_returns_empty_without_citations() -> None:
    """没有引用不是错误：正常答案、以及拒答文案，都应返回空列表且不抛异常。"""
    assert citation_issues("资料库中未找到相关信息。", 5) == []


def test_citation_issues_dedupes_and_sorts() -> None:
    """重复的越界编号只报一次，且按升序（便于日志比对与前端去重）。"""
    assert citation_issues("见 [9]、[7]、以及又一次 [9]", 3) == [7, 9]


def test_citation_issues_ignores_markdown_links() -> None:
    """`[1](http://…)` 是 markdown 链接，不是引用编号，不能误报。"""
    assert citation_issues("参见 [1](http://example.com/a)", 5) == []
    assert citation_issues("参见 [1](http://example.com/a)", 0) == []


def test_citation_issues_handles_multi_digit_numbers() -> None:
    """两位数编号按数值比较，不能按字符串首字符比较。"""
    assert citation_issues("见 [10]", 10) == []
    assert citation_issues("见 [11]", 10) == [11]


def test_citation_issues_flags_everything_when_no_source() -> None:
    """零来源时任何编号都是越界（拒答路径不该出现引用）。"""
    assert citation_issues("根据 [1] 可知", 0) == [1]


# ---------------------------------------------------------------- 7. 引用校验贯通到 API 与流式


def test_pipeline_ask_reports_citation_issues(
    pipeline_env: tuple[Settings, sessionmaker, int, int],
) -> None:
    """非流式：RagAnswer 必须带上越界编号，否则调用方无从判断引用是否可点。"""
    settings, session_factory, kb_id, doc_id = pipeline_env
    store = _StubStore(
        [
            ScoredChunk(
                document_id=doc_id,
                kb_id=kb_id,
                chunk_index=0,
                content="供应商须提供营业执照。",
                score=0.9,
            )
        ]
    )

    class _BadCiteLLM(_RecordingLLM):
        def complete(self, prompt, *, max_tokens=None, system=None) -> str:  # type: ignore[override]
            self.calls.append({"prompt": prompt, "system": system, "max_tokens": max_tokens})
            return "结论见 [1]，补充见 [8]。"  # 只有 1 个来源，[8] 越界

    rag = RagPipeline(
        settings=settings,
        session_factory=session_factory,
        vector_store=store,
        embedding=MockEmbedding(),
        llm=_BadCiteLLM(),
    )

    ans = rag.ask(kb_id, "供应商准入条件？")

    assert ans.citation_issues == [8]


def test_pipeline_stream_done_event_reports_citation_issues(
    pipeline_env: tuple[Settings, sessionmaker, int, int],
) -> None:
    """流式是主路径：done 事件不带 citation_issues 的话，流式用户永远看不到这个信号。"""
    settings, session_factory, kb_id, doc_id = pipeline_env
    store = _StubStore(
        [
            ScoredChunk(
                document_id=doc_id,
                kb_id=kb_id,
                chunk_index=0,
                content="供应商须提供营业执照。",
                score=0.9,
            )
        ]
    )

    class _BadCiteLLM(_RecordingLLM):
        def stream(self, prompt, *, max_tokens=None, system=None):  # type: ignore[override]
            self.calls.append({"prompt": prompt, "system": system, "max_tokens": max_tokens})
            yield "结论见 [1]，补充见 [8]。"

    rag = RagPipeline(
        settings=settings,
        session_factory=session_factory,
        vector_store=store,
        embedding=MockEmbedding(),
        llm=_BadCiteLLM(),
    )

    events = list(rag.ask_stream(kb_id, "供应商准入条件？"))

    done = [e for e in events if e["type"] == "done"][0]
    assert done["citation_issues"] == [8]


# ---------------------------------------------------------------- 8. 拒答分级（批 2-2）


def test_system_prompt_grades_out_of_scope_questions() -> None:
    """①级：资料与问题毫无关系时才整体拒答。

    「资料库中未找到相关信息」这个字面文案是**硬约束**——`NO_HIT_ANSWER` 常量与
    `scripts/k3_eval.py` 的 `REFUSAL_MARK` 都靠它判分，改文案会把评测改坏。
    """
    assert "完全无关" in SYSTEM_PROMPT
    assert "资料库中未找到相关信息" in SYSTEM_PROMPT


def test_system_prompt_grades_partial_coverage() -> None:
    """②级：资料只覆盖问题的一部分时，答已覆盖的部分 + 点明缺口，不许用常识补齐。

    没有这一级时模型只有两个坏选择：硬凑一个答案，或者整段拒答把有用的部分也丢掉。
    """
    assert "只覆盖" in SYSTEM_PROMPT
    assert "没有提及" in SYSTEM_PROMPT


def test_system_prompt_grades_conflicting_sources() -> None:
    """③级：两份资料互相矛盾时必须把冲突摆出来，不许擅自选一个当结论。

    这是最容易被模型悄悄抹平的情况——只输出其中一个说法，用户根本不知道还有另一种。
    """
    assert "冲突" in SYSTEM_PROMPT
    assert "不一致" in SYSTEM_PROMPT
    assert "擅自" in SYSTEM_PROMPT


def test_system_prompt_still_requires_naming_source_and_no_fabrication() -> None:
    """回归护栏：分级不能把批 1 的两条硬要求挤掉（点名文档名、不得编造）。

    批 1 实测：只要求标 [N] 时点名率 0/10，改成「句中写文档名」后 10/10。
    """
    assert "文档名" in SYSTEM_PROMPT
    assert "章节" in SYSTEM_PROMPT
    assert "编造" in SYSTEM_PROMPT


# ---------------------------------------------------------------- 9. 命中阈值（批 2-3）


def test_default_similarity_threshold_separates_unrelated_from_related() -> None:
    """默认阈值必须落在「库外噪声带」与「库内最弱支撑切片」之间的间隙里。

    实测（kb_1，hybrid 无关，本测试只看**向量路** raw 余弦，13 问固定问题集与
    `scripts/k3_eval.py` 同源，探针为 `scripts/measure_similarity_margin.py`）：

    - **第一次测量**（4 份文档 / 44 切片）：库外最高 **0.371**，库内 rank-5 最低 **0.469**
    - **语料改写后重测**（4 份文档 / 63 切片，2026-09-27）：库外最高 **0.375**，
      库内 rank-5 最低 **0.512**

    注意 K0 报的「库内 top1 0.70–0.79」只是第 1 名；第 5 名会低得多，
    不能拿第 1 名推断安全边界。旧默认 0.1 太低：库外问题照样拿满 5 条噪声进 prompt
    （拒答全靠模型自觉）。

    ⚠️ 这两个边界值是**本演示语料 + 当前 embedding** 的实测结果。换语料、加文档或
    换 embedding 供应商后**必须重跑 `scripts/measure_similarity_margin.py`**；
    语料改写这一轮正是因为改了语料才去重测（间隙反而变宽，0.40 无需调整）。
    """
    t = Settings(_env_file=None).similarity_threshold
    assert t > 0.375, f"阈值 {t} 落在库外噪声带内（实测库外最高 0.375），无关切片会进 prompt"
    assert t < 0.512, f"阈值 {t} 会切掉库内最弱的支撑切片（实测 rank-5 最低 0.512）"

