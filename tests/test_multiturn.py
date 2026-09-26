"""J1：多轮对话测试 —— 查询改写（规则/LLM 双策略）+ 历史上下文 + pipeline 检索词 + API 校验。

分层：
- 单元：QueryRewriter（无历史恒等 / 规则拼接 / 自包含保留 / LLM 改写与回退 / build_rewriter 装配）
- 单元：Generator prompt 组装（历史块顺序、单轮回归、Mock 解析不污染）
- pipeline（mock+chroma+sqlite 隔离环境）：ask 带 history 用改写词检索，rewritten_query 正确
- 集成（PG 可达才跑，test_inspect 模式）：ask/ask_stream 带 history、422 校验
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.main import app
from app.providers.factory import build_embedding, build_llm, build_reranker
from app.rag.generator import Generator
from app.rag.pipeline import RagPipeline
from app.rag.query_rewriter import QueryRewriter, build_rewriter
from app.storage.db import init_db
from app.storage.vector_store import ScoredChunk, build_vector_store


def _turn(role: str, content: str):
    return SimpleNamespace(role=role, content=content)


def _history(*items: tuple[str, str]) -> list:
    return [_turn(r, c) for r, c in items]


# ---- 工具：假 LLM（可注入，用于测试 LLM 改写策略） ----


class _FakeLLM:
    def __init__(self, output: str = "独立改写后的问句") -> None:
        self.output = output
        self.prompts: list[str] = []

    def complete(
        self, prompt: str, *, max_tokens: int | None = None, system: str | None = None
    ) -> str:
        self.prompts.append(prompt)
        return self.output


class _RaisingLLM:
    def complete(
        self, prompt: str, *, max_tokens: int | None = None, system: str | None = None
    ) -> str:
        raise RuntimeError("llm down")


# ================= QueryRewriter：单元 =================


def test_rewrite_no_history_identity() -> None:
    rw = QueryRewriter()
    assert rw.rewrite("报销流程是什么？") == "报销流程是什么？"
    assert rw.rewrite("报销流程是什么？", []) == "报销流程是什么？"


def test_rewrite_short_followup_combines_last_user_query() -> None:
    rw = QueryRewriter()
    hist = _history(("user", "报销流程是什么？"), ("assistant", "需要填写申请表"))
    # 短问句"那超过一万呢" → 拼接最近用户问句
    assert rw.rewrite("那超过一万呢？", hist) == "报销流程是什么？ · 那超过一万呢？"


def test_rewrite_pronoun_followup_combines() -> None:
    rw = QueryRewriter()
    hist = _history(("user", "出差住宿标准是多少钱"), ("assistant", "六百元"))
    assert rw.rewrite("那个呢？", hist) == "出差住宿标准是多少钱 · 那个呢？"


def test_rewrite_self_contained_long_kept() -> None:
    rw = QueryRewriter()
    hist = _history(("user", "报销流程是什么？"), ("assistant", "填写申请表"))
    q = "报销超过一万元需要经过哪些额外的审批环节和材料"
    assert len(q) > 20  # 自包含长问句
    assert rw.rewrite(q, hist) == q


def test_rewrite_uses_last_user_query() -> None:
    rw = QueryRewriter()
    hist = _history(
        ("user", "年假有几天？"),
        ("assistant", "五天"),
        ("user", "出差住宿标准呢？"),
        ("assistant", "六百元"),
    )
    assert rw.rewrite("再详细点", hist) == "出差住宿标准呢？ · 再详细点"


def test_rewrite_history_without_user_turn_identity() -> None:
    rw = QueryRewriter()
    hist = _history(("assistant", "你好"))
    assert rw.rewrite("那呢？", hist) == "那呢？"


# ---- LLM 策略 ----

def test_rewrite_llm_uses_prompt_and_returns_stripped() -> None:
    fake = _FakeLLM("  独立搜索问句：报销标准  \n")
    rw = QueryRewriter(fake, use_llm=True)
    hist = _history(("user", "报销标准是多少"), ("assistant", "六百元"))
    out = rw.rewrite("那超过一万呢？", hist)
    assert out == "独立搜索问句：报销标准"
    assert fake.prompts  # 确实调用了 LLM
    prompt = fake.prompts[0]
    assert "当前问句" in prompt and "报销标准是多少" in prompt
    assert "对话历史" in prompt and "六百元" in prompt


def test_rewrite_llm_fallback_on_mock_canned_output() -> None:
    # MockLLM 的兜底文案 → 视为改写失败，回退规则策略
    fake = _FakeLLM("资料库中未找到相关信息。")
    rw = QueryRewriter(fake, use_llm=True)
    hist = _history(("user", "报销流程是什么？"), ("assistant", "填写申请表"))
    assert rw.rewrite("那超过一万呢", hist) == "报销流程是什么？ · 那超过一万呢"


def test_rewrite_llm_fallback_on_empty_or_exception() -> None:
    rw_empty = QueryRewriter(_FakeLLM("   "), use_llm=True)
    hist = _history(("user", "报销流程是什么？"), ("assistant", "填写申请表"))
    assert rw_empty.rewrite("那超过一万呢", hist) == "报销流程是什么？ · 那超过一万呢"

    rw_err = QueryRewriter(_RaisingLLM(), use_llm=True)
    assert rw_err.rewrite("那超过一万呢", hist) == "报销流程是什么？ · 那超过一万呢"


def test_build_rewriter_mock_uses_rule() -> None:
    rw = build_rewriter(Settings(_env_file=None, rag_provider="mock"), _FakeLLM())
    assert rw._use_llm is False


def test_build_rewriter_real_uses_llm() -> None:
    rw = build_rewriter(Settings(_env_file=None, rag_provider="dashscope"), _FakeLLM())
    assert rw._use_llm is True


# ================= Generator：prompt 组装 =================

def _scored_chunks(*texts: str) -> list[ScoredChunk]:
    return [
        ScoredChunk(document_id=1, kb_id=1, chunk_index=i, content=t, score=0.8)
        for i, t in enumerate(texts)
    ]


def test_generator_no_history_matches_single_turn() -> None:
    g = Generator(_FakeLLM())
    chunks = _scored_chunks("差旅报销制度说明")
    p1 = g.build_prompt("出差住宿标准是多少？", chunks, history=None)
    p2 = g.build_prompt("出差住宿标准是多少？", chunks, history=[])
    assert p1 == p2  # None 与空列表等价
    assert "【对话历史】" not in p1
    # 单轮结构：资料在问题前，引用块完整
    assert p1.index("【资料】") < p1.index("【用户问题】")
    assert "[1] 差旅报销制度说明" in p1


def test_generator_history_block_order_and_format() -> None:
    g = Generator(_FakeLLM())
    chunks = _scored_chunks("出差住宿标准：一线城市六百元。")
    hist = _history(("user", "报销流程是什么？"), ("assistant", "需要填写申请表 [1]"))
    p = g.build_prompt("那超过一万呢？", chunks, history=hist)
    # 顺序：对话历史 → 资料 → 用户问题
    assert p.index("【对话历史】") < p.index("【资料】") < p.index("【用户问题】")
    assert "用户：报销流程是什么？" in p
    assert "助手：需要填写申请表 [1]" in p


def test_mockllm_parse_unaffected_by_history() -> None:
    """MockLLM 仍能正确解析带历史块的 prompt（历史里含 "[1]" 也不污染块解析）。"""
    from app.providers.mock import MockLLM

    g = Generator(MockLLM())
    chunks = _scored_chunks("出差住宿标准：一线城市每晚不超过六百元。")
    hist = _history(("user", "报销流程是什么？"), ("assistant", "需要填写申请表 [1]"))
    prompt = g.build_prompt("住宿标准是多少？", chunks, history=hist)
    query, blocks = MockLLM._parse_prompt(prompt)
    assert query == "住宿标准是多少？"
    assert len(blocks) == 1 and "六百元" in blocks[0]


# ================= pipeline（mock+chroma+sqlite 隔离环境） =================

DOC_TEXT = """# 测试知识库

## 差旅报销制度

出差住宿标准：一线城市每晚不超过六百元，其他城市每晚不超过四百元。交通费用凭票据实报销，市内交通费按每天五十元标准包干。

## 年假管理制度

年假按员工入职年限计算：入职满一年享五天，满三年享八天，满五年享十天。
"""


@pytest.fixture()
def env(tmp_path: Path) -> tuple[Settings, sessionmaker, RagPipeline, int]:
    settings = Settings(
        rag_provider="mock",
        data_dir=tmp_path / "data",
        chunk_size=200,
        chunk_overlap=20,
        top_k=3,
        vector_store="chroma",
    )
    _, session_factory = init_db(settings)

    from app.ingestion.pipeline import IngestionPipeline
    from app.core.models import User

    with session_factory() as db:
        user = db.query(User).filter(User.username == "_multiturn_test").first()
        if user is None:
            user = User(username="_multiturn_test", hashed_password="")
            db.add(user)
            db.flush()
        user_id = user.id

    vector_store = build_vector_store(settings)
    embedding = build_embedding(settings)
    llm = build_llm(settings)
    reranker = build_reranker(settings)

    ingest = IngestionPipeline(
        settings=settings,
        session_factory=session_factory,
        vector_store=vector_store,
        embedding=embedding,
    )
    rag = RagPipeline(
        settings=settings,
        session_factory=session_factory,
        vector_store=vector_store,
        embedding=embedding,
        llm=llm,
        reranker=reranker,
    )

    # 摄取一份文档
    p = tmp_path / "policy.md"
    p.write_text(DOC_TEXT, encoding="utf-8")
    kb = ingest.create_kb("多轮测试库", user_id=user_id)
    ingest.ingest_file(kb.id, p)
    return settings, session_factory, rag, kb.id


def test_ask_with_history_uses_rewritten_query(env) -> None:
    _, _, rag, kb_id = env
    hist = _history(
        ("user", "差旅报销需要什么手续？"),
        ("assistant", "凭票据实报销。"),
    )
    result = rag.ask(kb_id, "那住宿标准呢？", history=hist)
    assert result.rewritten_query == "差旅报销需要什么手续？ · 那住宿标准呢？"
    # 改写词命中住宿标准内容
    assert result.sources and "住宿标准" in result.sources[0].content
    # 答案生成用的是原始 query（MockLLM 提取【用户问题】）
    assert result.query == "那住宿标准呢？"


def test_ask_without_history_rewritten_is_query(env) -> None:
    _, _, rag, kb_id = env
    result = rag.ask(kb_id, "年假有几天？")
    assert result.rewritten_query == "年假有几天？"
    assert result.sources and "年假" in result.sources[0].content


def test_ask_self_contained_with_history_keeps_query(env) -> None:
    _, _, rag, kb_id = env
    hist = _history(("user", "年假有几天？"), ("assistant", "五天"))
    q = "出差住宿标准超过四百元需要经过哪些审批流程"
    assert len(q) > 20
    result = rag.ask(kb_id, q, history=hist)
    assert result.rewritten_query == q


# ================= API 集成（PG 可达才跑） =================


def _pg_reachable() -> bool:
    try:
        import psycopg2
        s = Settings()
        conn = psycopg2.connect(
            host=s.pg_host, port=s.pg_port, user=s.pg_user,
            password=s.pg_password, dbname=s.pg_database,
        )
        conn.close()
        return True
    except Exception:
        return False


def _register_and_login(client: TestClient, username: str) -> str:
    client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    return client.post("/api/auth/login", json={"username": username, "password": "secret123"}).json()["access_token"]


def _create_kb(client: TestClient, token: str, name: str) -> int:
    return client.post("/api/kbs", json={"name": name}, headers={"Authorization": f"Bearer {token}"}).json()["id"]


def _upload(client: TestClient, kb_id: int, token: str, filename: str, content: bytes):
    return client.post(
        f"/api/kbs/{kb_id}/documents",
        files={"file": (filename, io.BytesIO(content), "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def client() -> TestClient:
    if not _pg_reachable():
        pytest.skip("PostgreSQL 不可达，跳过 J1 多轮 API 测试")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    if not _pg_reachable():
        return
    import psycopg2
    s = Settings()
    conn = psycopg2.connect(
        host=s.pg_host, port=s.pg_port, user=s.pg_user,
        password=s.pg_password, dbname=s.pg_database,
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DELETE FROM knowledge_bases WHERE name LIKE 'test_j1%'")
    cur.execute("DELETE FROM users WHERE username LIKE 'test_j1%'")
    conn.close()


def test_ask_with_history_returns_rewritten_query(client: TestClient) -> None:
    token = _register_and_login(client, "test_j1_ask")
    kb_id = _create_kb(client, token, "test_j1_kb")
    _upload(client, kb_id, token, "policy.txt", "出差住宿标准：一线城市每晚不超过六百元。".encode("utf-8"))

    body = {
        "query": "那超过一万呢？",
        "history": [
            {"role": "user", "content": "出差住宿标准是多少？"},
            {"role": "assistant", "content": "一线城市六百元。"},
        ],
    }
    resp = client.post(f"/api/kbs/{kb_id}/ask", json=body, headers=_auth(token))
    assert resp.status_code == 200
    data = resp.json()
    assert data["rewritten_query"] == "出差住宿标准是多少？ · 那超过一万呢？"
    assert data["query"] == "那超过一万呢？"
    assert len(data["answer"]) > 0


def test_ask_without_history_rewritten_is_query(client: TestClient) -> None:
    token = _register_and_login(client, "test_j1_plain")
    kb_id = _create_kb(client, token, "test_j1_plain_kb")
    _upload(client, kb_id, token, "policy.txt", "年假满一年享五天。".encode("utf-8"))

    resp = client.post(f"/api/kbs/{kb_id}/ask", json={"query": "年假有几天？"}, headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["rewritten_query"] == "年假有几天？"


def test_ask_history_invalid_role_422(client: TestClient) -> None:
    token = _register_and_login(client, "test_j1_badrole")
    kb_id = _create_kb(client, token, "test_j1_badrole_kb")

    resp = client.post(
        f"/api/kbs/{kb_id}/ask",
        json={"query": "test", "history": [{"role": "system", "content": "hi"}]},
        headers=_auth(token),
    )
    assert resp.status_code == 422


def test_ask_history_over_limit_422(client: TestClient) -> None:
    token = _register_and_login(client, "test_j1_overflow")
    kb_id = _create_kb(client, token, "test_j1_overflow_kb")

    hist = [{"role": "user", "content": "x"}] * 21  # 上限 20
    resp = client.post(
        f"/api/kbs/{kb_id}/ask",
        json={"query": "test", "history": hist},
        headers=_auth(token),
    )
    assert resp.status_code == 422


def test_ask_stream_with_history_includes_rewritten_query(client: TestClient) -> None:
    token = _register_and_login(client, "test_j1_stream")
    kb_id = _create_kb(client, token, "test_j1_stream_kb")
    _upload(client, kb_id, token, "policy.txt", "出差住宿标准：一线城市每晚不超过六百元。".encode("utf-8"))

    body = {
        "query": "那超过一万呢？",
        "history": [{"role": "user", "content": "出差住宿标准是多少？"}],
    }
    resp = client.post(f"/api/kbs/{kb_id}/ask/stream", json=body, headers=_auth(token))
    assert resp.status_code == 200

    rewritten_query = None
    for line in resp.text.strip().split("\n\n"):
        if not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str == "[DONE]":
            continue
        payload = json.loads(data_str)
        if payload.get("type") == "sources":
            rewritten_query = payload.get("rewritten_query")
    assert rewritten_query == "出差住宿标准是多少？ · 那超过一万呢？"
