"""K2 管线单例与连接复用：provider / 向量库 / 管线在应用启动时装配一次，请求期复用。

离线可跑：monkeypatch 把 DATA_DIR 指到 tmp_path 并全量 mock 供应商，
不碰真实 data/ 目录、不连 PostgreSQL、不出网。

关键手法：**必须在 TestClient 之前打桩**。lifespan 自己也会调用
build_llm / build_vector_store，若在进入上下文之后再打桩就数不到装配期那一次。
"""
from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------- 装配计数环境


@pytest.fixture()
def counted(tmp_path, monkeypatch) -> Iterator[tuple[TestClient, dict[str, int]]]:
    """在 app 启动前给四个装配点打桩计数，随后进入 lifespan。"""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VECTOR_STORE", "chroma")
    monkeypatch.setenv("RAG_PROVIDER", "mock")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("LLM_PROVIDER", "mock")

    from app.ingestion import pipeline as ingestion_mod
    from app.providers import factory
    from app.storage import vector_store as vs_mod

    counts = {"llm": 0, "embedding": 0, "vector_store": 0, "ingestion": 0}
    real_llm = factory.build_llm
    real_embedding = factory.build_embedding
    real_vector_store = vs_mod.build_vector_store
    real_ingestion = ingestion_mod.IngestionPipeline

    def counting_llm(settings=None):
        counts["llm"] += 1
        return real_llm(settings)

    def counting_embedding(settings=None):
        counts["embedding"] += 1
        return real_embedding(settings)

    def counting_vector_store(settings=None):
        counts["vector_store"] += 1
        return real_vector_store(settings)

    class CountingIngestion(real_ingestion):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs) -> None:
            counts["ingestion"] += 1
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(factory, "build_llm", counting_llm)
    monkeypatch.setattr(factory, "build_embedding", counting_embedding)
    monkeypatch.setattr(vs_mod, "build_vector_store", counting_vector_store)
    monkeypatch.setattr(ingestion_mod, "IngestionPipeline", CountingIngestion)

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, counts


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _register_and_login(client: TestClient, username: str) -> str:
    client.post("/api/auth/register", json={"username": username, "password": "secret123"})
    return client.post(
        "/api/auth/login", json={"username": username, "password": "secret123"}
    ).json()["access_token"]


def _create_kb(client: TestClient, token: str, name: str = "k2_kb") -> int:
    return client.post("/api/kbs", json={"name": name}, headers=_auth(token)).json()["id"]


def _upload_policy(client: TestClient, kb_id: int, token: str) -> None:
    client.post(
        f"/api/kbs/{kb_id}/documents",
        files={
            "file": (
                "policy.txt",
                "出差住宿标准：一线城市每晚不超过六百元，其他城市不超过四百元。".encode("utf-8"),
                "text/plain",
            )
        },
        headers=_auth(token),
    )


# ---------------------------------------------------------------- 1. 装配期


def test_assembly_builds_each_dependency_once(
    counted: tuple[TestClient, dict[str, int]],
) -> None:
    """应用启动即装配：llm / embedding / vector_store / IngestionPipeline 各构造恰好 1 次。"""
    _, counts = counted
    assert counts["llm"] == 1
    assert counts["embedding"] == 1
    assert counts["vector_store"] == 1
    assert counts["ingestion"] == 1


def test_rag_and_ingestion_share_the_same_store_and_embedding(
    counted: tuple[TestClient, dict[str, int]],
) -> None:
    """摄取与问答必须共用同一份向量库与 embedding —— 否则等于两套 engine / httpx 池。"""
    from app.main import app

    assert app.state.ingestion.vector_store is app.state.rag.vector_store
    assert app.state.ingestion.embedding is app.state.rag.embedding


# ---------------------------------------------------------------- 2. 请求期复用


def test_all_request_paths_reuse_singletons(
    counted: tuple[TestClient, dict[str, int]],
) -> None:
    """★ 核心断言：上传 + 10 次问答 + 质检 + 体检，四个装配计数全程保持 1。

    改造前：每次 /ask 重建 llm；/inspect、/diagnostics、上传各再建一份 vector_store。
    """
    client, counts = counted
    token = _register_and_login(client, "test_k2_reuse")
    kb_id = _create_kb(client, token)

    _upload_policy(client, kb_id, token)
    for _ in range(10):
        resp = client.post(
            f"/api/kbs/{kb_id}/ask",
            json={"query": "出差住宿标准是多少？"},
            headers=_auth(token),
        )
        assert resp.status_code == 200

    assert (
        client.post(
            f"/api/kbs/{kb_id}/inspect", json={"query": "住宿标准"}, headers=_auth(token)
        ).status_code
        == 200
    )
    assert client.get(f"/api/kbs/{kb_id}/diagnostics", headers=_auth(token)).status_code == 200

    assert counts["llm"] == 1
    assert counts["embedding"] == 1
    assert counts["vector_store"] == 1
    assert counts["ingestion"] == 1


def test_delete_kb_reuses_singleton_store(
    counted: tuple[TestClient, dict[str, int]],
) -> None:
    """删知识库要清理向量集合，但不应为此再建一个向量库连接。"""
    client, counts = counted
    token = _register_and_login(client, "test_k2_delete")
    kb_id = _create_kb(client, token)
    _upload_policy(client, kb_id, token)

    before = counts["vector_store"]
    assert client.delete(f"/api/kbs/{kb_id}", headers=_auth(token)).status_code == 200
    assert counts["vector_store"] == before == 1


# ---------------------------------------------------------------- 3. 取用垫片


def test_get_rag_raises_clear_error_when_state_missing() -> None:
    """未触发 lifespan 就取用（如裸访问 app.state）时，要报清晰错误而非 AttributeError。"""
    from app.api.deps import get_rag

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    with pytest.raises(RuntimeError, match="rag"):
        get_rag(request)


# ---------------------------------------------------------------- 4. 连接与超时显式化


def test_openai_clients_have_explicit_timeout_and_retries() -> None:
    """★ 修真实风险：此前完全吃 SDK 默认（读超时 600s、无上限），真模型挂住会一直等。"""
    from app.providers import openai_compat as oc

    def read_timeout(client) -> float:
        """SDK 视传入值类型决定存 float 还是 httpx.Timeout，两种都取到读超时。"""
        timeout = client.timeout
        return float(getattr(timeout, "read", timeout))

    llm = oc.OpenAICompatLLM(base_url="http://127.0.0.1:1/v1", api_key="sk-test", model="m")
    embedding = oc.OpenAICompatEmbedding(
        base_url="http://127.0.0.1:1/v1", api_key="sk-test", model="m", dim=8
    )

    assert oc._MAX_RETRIES == 2
    assert llm._client.max_retries == oc._MAX_RETRIES
    assert embedding._client.max_retries == oc._MAX_RETRIES
    assert read_timeout(llm._client) == oc._TIMEOUT_SECONDS
    assert read_timeout(embedding._client) == oc._TIMEOUT_SECONDS


def test_pgvector_engine_pool_is_tuned() -> None:
    """★ 连接复用后要防「拿到空闲过久的死连接」：pool_pre_ping 与池上限必须显式设定。

    engine 是惰性的（此处不连库），因此本用例无需 PostgreSQL 即可跑。
    """
    from sqlalchemy.pool import QueuePool

    from app.config import Settings
    from app.storage.pgvector_store import PgVectorStore

    pool = PgVectorStore(settings=Settings(vector_store="pgvector")).engine.pool

    assert isinstance(pool, QueuePool)
    assert pool._pre_ping is True       # 借用前 ping，避免空闲断连后的首个请求报错
    assert pool._max_overflow == 10

