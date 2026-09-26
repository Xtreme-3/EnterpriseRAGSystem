"""K8 可观测性与错误提示：日志可配、请求可追踪、静默点开口。

全部**离线可跑**（mock 供应商 + 临时 SQLite / 临时 chroma，不依赖 PostgreSQL / 真实 Key）。

本文件覆盖三类回归：

1. **日志基建**（K8-1）：级别可配、只配 root、`app` 向 root 传播、重复装配不叠加 handler。
   旧实现把 `setLevel(INFO)` 硬编码在 `app/main.py`，且 `propagate=False`，
   于是 DEBUG 永久静默、第三方 logger 完全不受治理。
2. **请求可追踪**（K8-2）：每个响应带 `X-Request-ID`，客户端传入则原样回显；
   未处理异常的 500 响应体里带同一个 id —— 否则用户截图「服务器内部错误」，
   运维只能靠时间戳猜是哪条日志。
3. **静默点开口**（K8-3 ~ K8-7）：非流式问答失败、鉴权 401、向量零命中、
   查询改写失败、重排退化、摄取失败、流式截断、摄取收尾失败 —— 逐个验证留下痕迹。
"""
from __future__ import annotations

import logging
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.storage.db import init_db

# ---------------------------------------------------------------- 夹具


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    """真实 app，但把元数据库换成临时 SQLite。

    走 `TestClient(app)` 触发 lifespan 装配（K2 的管线单例），
    再把 `app.state.session_factory` 指到临时库，避免污染开发库 `data/rag.db`。
    """
    settings = Settings(
        _env_file=None, vector_store="chroma", data_dir=tmp_path, rag_provider="mock", rerank=False
    )
    _, session_factory = init_db(settings)
    with TestClient(app, raise_server_exceptions=False) as c:
        monkeypatch.setattr(app.state, "session_factory", session_factory)
        yield c


def _register_and_login(client: TestClient, username: str) -> str:
    client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    resp = client.post("/api/auth/login", json={"username": username, "password": "secret123"})
    return resp.json()["access_token"]


def _create_kb(client: TestClient, token: str, name: str) -> int:
    resp = client.post("/api/kbs", json={"name": name}, headers={"Authorization": f"Bearer {token}"})
    return resp.json()["id"]


def _messages(records: list[logging.LogRecord]) -> str:
    return " | ".join(str(r.getMessage()) for r in records)


# ---------------------------------------------------------------- K8-1 日志基建可配


def test_settings_expose_log_level_defaulting_to_info() -> None:
    assert Settings(_env_file=None).log_level == "INFO"


def test_settings_normalize_log_level_case() -> None:
    """`.env` 里写 `debug` 也要能用（大小写不该成为启动失败的坑）。"""
    assert Settings(_env_file=None, log_level="debug").log_level == "DEBUG"


def test_settings_reject_unknown_log_level() -> None:
    """拼错的级别名必须在**启动期**炸，而不是被 getattr 静默降级成 INFO。"""
    with pytest.raises(Exception):
        Settings(_env_file=None, log_level="verbose")


def test_setup_logging_puts_a_single_handler_on_root() -> None:
    """只配 app 记录器时，uvicorn / httpx / chromadb 的 WARNING 会走
    logging.lastResort（无时间戳、无级别、无 logger 名），全进程日志格式不统一。"""
    from app.logging_config import setup_logging

    setup_logging("INFO")
    root = logging.getLogger()
    managed = [h for h in root.handlers if getattr(h, "_rag_managed", False)]
    assert len(managed) == 1


def test_setup_logging_makes_app_propagate_to_root() -> None:
    """`app` 必须向上传播，否则 root 上的 handler（含 pytest 的 caplog）收不到 app 日志。"""
    from app.logging_config import setup_logging

    setup_logging("INFO")
    assert logging.getLogger("app").propagate is True
    assert logging.getLogger("app").handlers == []


def test_setup_logging_is_idempotent() -> None:
    """重复装配（lifespan 重入、多次 import）不能叠加 handler，否则一行日志打三遍。"""
    from app.logging_config import setup_logging

    setup_logging("INFO")
    setup_logging("DEBUG")
    root = logging.getLogger()
    managed = [h for h in root.handlers if getattr(h, "_rag_managed", False)]
    assert len(managed) == 1
    assert logging.getLogger("app").level == logging.DEBUG


def test_debug_message_is_visible_when_log_level_is_debug(caplog) -> None:
    """LOG_LEVEL=DEBUG 时 app.* 的 debug 消息必须能出来（排查零命中/退化的唯一手段）。"""
    from app.logging_config import setup_logging

    setup_logging("DEBUG")
    logging.getLogger("app.storage.vector_store").debug("零命中：丢弃 5 条低于阈值的切片")
    assert any("零命中" in r.getMessage() for r in caplog.records)


def test_debug_message_is_dropped_at_info_level(caplog) -> None:
    from app.logging_config import setup_logging

    setup_logging("INFO")
    logging.getLogger("app.storage.vector_store").debug("这条不该出现")
    assert not any("这条不该出现" in r.getMessage() for r in caplog.records)


def test_third_party_logger_records_reach_root_handler(caplog) -> None:
    """第三方 logger（httpx 等）不再各自为政：它们的记录也要经过同一套 handler。"""
    from app.logging_config import setup_logging

    setup_logging("INFO")
    logging.getLogger("httpx").warning("连接池耗尽")
    assert any("连接池耗尽" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------- K8-2 request-id


def test_response_carries_request_id_header(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.headers.get("X-Request-ID")


def test_client_supplied_request_id_is_echoed(client: TestClient) -> None:
    """上游网关/前端传入的 id 必须原样透传，否则跨系统追踪链会断。"""
    resp = client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert resp.headers["X-Request-ID"] == "trace-abc-123"


def test_unhandled_exception_response_carries_request_id(client: TestClient, monkeypatch) -> None:
    """500 响应体必须带 request_id：这是用户能提供、运维能搜索的唯一线索。"""
    monkeypatch.setattr(app.state, "session_factory", None)  # get_db 直接炸 → 未处理异常
    resp = client.get(
        "/api/kbs",
        headers={"Authorization": "Bearer x", "X-Request-ID": "trace-500"},
    )
    assert resp.status_code == 500
    body = resp.json()
    assert body["request_id"] == "trace-500"
    assert resp.headers["X-Request-ID"] == "trace-500"


def test_request_id_is_attached_to_log_records(client: TestClient, caplog) -> None:
    """日志行要带上 request_id，才能把"客户端报错"和"服务端日志"对上号。"""
    with caplog.at_level(logging.WARNING, logger="app.api.deps"):
        client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer not-a-jwt", "X-Request-ID": "trace-log"},
        )
    assert any(getattr(r, "request_id", None) == "trace-log" for r in caplog.records)


# ---------------------------------------------------------------- K8-3 非流式问答失败


def test_non_stream_ask_failure_is_logged(client: TestClient, caplog, monkeypatch) -> None:
    """流式路径有 logger.exception（chat.py:455），非流式却是裸 raise —— 两种待遇必须拉平。"""
    token = _register_and_login(client, "test_k8_ask")
    kb_id = _create_kb(client, token, "test_k8_ask_kb")

    def boom(*_args, **_kwargs):
        raise RuntimeError("embedding 服务掉线")

    monkeypatch.setattr(app.state.rag, "ask", boom)

    with caplog.at_level(logging.ERROR, logger="app.chat"):
        resp = client.post(
            f"/api/kbs/{kb_id}/ask",
            json={"query": "出差住宿标准是多少？"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 502
    assert resp.json()["detail"] == "问答服务暂时不可用"
    # 原始异常只出现在堆栈里（logger.exception 不带 exc 参数），
    # 所以断言 caplog.text 而不是 getMessage()
    assert "embedding 服务掉线" in caplog.text
    assert any(r.levelno >= logging.ERROR and r.name == "app.chat" for r in caplog.records)


# ---------------------------------------------------------------- K8-4 鉴权 401


def test_invalid_token_401_is_logged(client: TestClient, caplog) -> None:
    """token 无效/过期完全无日志时，无法区分"用户 token 过期"和"JWT_SECRET 配错了"。"""
    with caplog.at_level(logging.WARNING, logger="app.api.deps"):
        resp = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401
    assert any("令牌" in r.getMessage() for r in caplog.records)


def test_login_failure_is_logged(client: TestClient, caplog) -> None:
    """登录失败是安全事件：没有日志就没有暴力破解的审计线索。"""
    with caplog.at_level(logging.WARNING, logger="app.api.auth"):
        resp = client.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})
    assert resp.status_code == 401
    assert any("登录失败" in r.getMessage() for r in caplog.records)
    assert "wrong" not in _messages(caplog.records)  # 绝不记密码


# ---------------------------------------------------------------- K8-5 静默点开口


def test_chroma_search_logs_dropped_low_score_hits(tmp_path, caplog) -> None:
    """"我搜不到东西"是最高频报障，而低于阈值的丢弃此前完全静默 —— 零线索。"""
    from app.storage.vector_store import ChromaVectorStore, ChunkToIndex

    store = ChromaVectorStore(tmp_path / "chroma", min_score=0.99)
    store.ensure_collection(1, 4)
    store.add(
        1,
        [
            ChunkToIndex(
                id="1:0", content="无关内容", vector=[1.0, 0.0, 0.0, 0.0],
                document_id=1, kb_id=1, chunk_index=0,
            )
        ],
    )

    with caplog.at_level(logging.DEBUG, logger="app.storage.vector_store"):
        hits = store.search(1, [0.0, 1.0, 0.0, 0.0], top_k=5)

    assert hits == []
    assert any("低于阈值" in r.getMessage() for r in caplog.records)


def test_rewriter_logs_llm_failure_and_falls_back(caplog) -> None:
    """改写失败会静默退回原 query：多轮问答效果变差，却无任何痕迹。"""
    from app.rag.query_rewriter import QueryRewriter
    from types import SimpleNamespace

    class _BoomLLM:
        def complete(self, *_args, **_kwargs):
            raise RuntimeError("改写模型 502")

    turns = [SimpleNamespace(role="user", content="出差住宿标准是多少")]
    rewriter = QueryRewriter(_BoomLLM(), use_llm=True)

    with caplog.at_level(logging.WARNING, logger="app.rag.query_rewriter"):
        out = rewriter.rewrite("那超过一万呢", history=turns)

    assert out == "出差住宿标准是多少 · 那超过一万呢"  # 降级到规则策略，行为不变
    assert any("改写" in r.getMessage() for r in caplog.records)


def test_rerank_parse_logs_missing_results(caplog) -> None:
    """重排返回缺字段时静默填 0，排序退化为原序 —— 用户只会觉得"搜得不准"。"""
    from app.providers.factory import DashScopeRerank, OpenAICompatRerank

    with caplog.at_level(logging.WARNING, logger="app.providers.factory"):
        openai_scores = OpenAICompatRerank._parse({"results": []}, n=3)
        dashscope_scores = DashScopeRerank._parse({"output": {}}, n=3)

    assert openai_scores == [0.0, 0.0, 0.0]
    assert dashscope_scores == [0.0, 0.0, 0.0]
    assert sum("重排" in r.getMessage() for r in caplog.records) >= 2


def test_ingestion_failure_is_logged(tmp_path, caplog) -> None:
    """摄取失败此前只写 DB 的 doc.error、不打日志：服务端日志里完全无痕。"""
    from app.ingestion.pipeline import IngestionPipeline
    from app.providers.mock import MockEmbedding
    from app.storage.vector_store import ChromaVectorStore

    settings = Settings(
        _env_file=None, vector_store="chroma", data_dir=tmp_path, rag_provider="mock"
    )
    _, session_factory = init_db(settings)
    pipeline = IngestionPipeline(
        settings=settings,
        session_factory=session_factory,
        vector_store=ChromaVectorStore(tmp_path / "chroma"),
        embedding=MockEmbedding(),
    )
    kb = pipeline.create_kb("test_k8_ingest")
    bad = tmp_path / "unsupported.xyz"
    bad.write_text("随便什么内容", encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="app.ingestion.pipeline"):
        doc = pipeline.ingest_file(kb.id, bad)

    assert doc.status == "failed"
    assert any("unsupported.xyz" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------- K8-6 流式截断


def test_stream_warns_when_answer_truncated_by_token_limit(caplog) -> None:
    """`complete()` 有截断告警，`stream()` 没有 —— 流式回答被砍断时用户只会看到半句话。"""
    from types import SimpleNamespace

    from app.providers.openai_compat import OpenAICompatLLM

    chunks = [
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="结论："), finish_reason=None)]
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=""), finish_reason="length")]
        ),
    ]
    llm = OpenAICompatLLM(base_url="http://stub/v1", api_key="k", model="m", max_tokens=2048)
    llm._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **_kw: iter(chunks))
        )
    )

    with caplog.at_level(logging.WARNING, logger="app.providers.openai_compat"):
        out = "".join(llm.stream("问题"))

    assert out == "结论："  # 已有内容照常产出，不吞
    assert any("截断" in r.getMessage() or "length" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------- K8-7 摄取收尾失败


def test_finalize_failure_marks_document_failed_not_processing(tmp_path, caplog, monkeypatch) -> None:
    """step4 置 indexed 那段没有 try/except：一旦此处抛错，文档永久卡在 processing，
    用户既看不到失败原因，也没有重试入口。"""
    from app.ingestion import pipeline as mod
    from app.ingestion.pipeline import IngestionPipeline
    from app.providers.mock import MockEmbedding
    from app.storage.vector_store import ChromaVectorStore

    settings = Settings(
        _env_file=None, vector_store="chroma", data_dir=tmp_path, rag_provider="mock"
    )
    _, session_factory = init_db(settings)
    pipeline = IngestionPipeline(
        settings=settings,
        session_factory=session_factory,
        vector_store=ChromaVectorStore(tmp_path / "chroma"),
        embedding=MockEmbedding(),
    )
    kb = pipeline.create_kb("test_k8_finalize")
    doc_file = tmp_path / "ok.txt"
    doc_file.write_text("出差住宿标准：一线城市每晚不超过六百元。", encoding="utf-8")

    real_get_db = mod.get_db
    calls = {"n": 0}

    @contextmanager
    def flaky(session_factory_arg):
        calls["n"] += 1
        if calls["n"] == 2:  # 第 1 次 = step2 写元数据；第 2 次 = step4 置 indexed
            raise RuntimeError("数据库连接断开")
        with real_get_db(session_factory_arg) as db:
            yield db

    monkeypatch.setattr(mod, "get_db", flaky)

    with caplog.at_level(logging.ERROR, logger="app.ingestion.pipeline"):
        doc = pipeline.ingest_file(kb.id, doc_file)

    assert doc.status == "failed"
    assert "数据库连接断开" in doc.error
    assert "数据库连接断开" in _messages(caplog.records)
