"""模型供应商插槽装配：多供应商解析 + 插槽级覆盖 + embedding 维度自检（K0）。

背景：LLM 留在第三方中转站（只有对话模型），Embedding 换到阿里云百炼官方。
三个插槽必须能各自指向不同供应商——此前 ``build_reranker`` 跟随 LLM 槽位解析
端点，embedding 一旦换家，rerank 就会被打到**没有 rerank 接口**的中转站上。

全部离线：真实 httpx 客户端由 ``fake_openai`` 夹具替换（每个真实 client 约 650ms，
13 条用例会白花十几秒）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.providers import openai_compat
from app.providers.factory import (
    DashScopeRerank,
    OpenAICompatEmbedding,
    OpenAICompatRerank,
    _resolve_endpoint,
    build_embedding,
    build_llm,
    build_reranker,
)

_RELAY_URL = "https://relay.example/v1"
_BAILIAN_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_NATIVE_RERANK = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"


def _settings(**overrides) -> Settings:
    """「LLM 走中转站、Embedding 走百炼」的标准配置；不读 .env 文件。"""
    base: dict = {
        "_env_file": None,
        "rag_provider": "dashscope",
        "dashscope_api_key": "sk-relay",
        "dashscope_base_url": _RELAY_URL,
        "bailian_api_key": "sk-bailian",
        "bailian_base_url": _BAILIAN_URL,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def fake_openai(monkeypatch) -> list[tuple[str, str]]:
    """替换 OpenAI 客户端构造，记录 (base_url, api_key)，避免真建 SSLContext。"""
    calls: list[tuple[str, str]] = []

    def _fake_make_client(*, base_url: str, api_key: str):
        calls.append((base_url, api_key))
        return SimpleNamespace(base_url=base_url, api_key=api_key)

    monkeypatch.setattr(openai_compat, "_make_client", _fake_make_client)
    return calls


# ---- 供应商注册：bailian 与 dashscope 是两个独立槽位 ----------------


def test_bailian_resolves_to_official_endpoint() -> None:
    """bailian 必须解析到百炼官方端点，而不是 dashscope 槽位指向的中转站。"""
    assert _resolve_endpoint(_settings(), "bailian") == (_BAILIAN_URL, "sk-bailian")


def test_bailian_missing_key_raises_with_field_name() -> None:
    """缺 key 的报错要直接点名要填哪个字段。"""
    with pytest.raises(ValueError, match="BAILIAN_API_KEY"):
        build_embedding(_settings(embedding_provider="bailian", bailian_api_key=""))


def test_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="不支持"):
        build_embedding(_settings(embedding_provider="openai"))


def test_embedding_slot_and_llm_slot_are_independent(fake_openai) -> None:
    """embedding 走百炼、LLM 仍走中转站——两个插槽互不串台。"""
    s = _settings(embedding_provider="bailian", llm_provider="dashscope")
    build_embedding(s)
    build_llm(s)
    assert (_BAILIAN_URL, "sk-bailian") in fake_openai
    assert (_RELAY_URL, "sk-relay") in fake_openai


# ---- rerank 独立槽位（本次修的真实缺陷） ---------------------------


def test_rerank_provider_overrides_llm_slot() -> None:
    """LLM 在中转站，rerank 仍可单独指定百炼（走原生端点，不是 compatible）。"""
    s = _settings(llm_provider="dashscope", rerank_provider="bailian", rerank=True)
    r = build_reranker(s)
    assert isinstance(r, DashScopeRerank)
    assert r._endpoint == _NATIVE_RERANK


def test_rerank_follows_llm_slot_when_provider_unset() -> None:
    """回归：不设 RERANK_PROVIDER 时行为与改动前完全一致（跟随 LLM 槽位）。"""
    s = _settings(llm_provider="dashscope", rerank=True)
    r = build_reranker(s)
    assert isinstance(r, OpenAICompatRerank)
    assert r._base_url == _RELAY_URL


def test_rerank_provider_defaults_to_empty() -> None:
    assert Settings(_env_file=None).rerank_provider == ""


def test_bailian_base_url_defaults_to_official() -> None:
    assert Settings(_env_file=None).bailian_base_url == _BAILIAN_URL


# ---- embedding 维度自检 --------------------------------------------


class _Item:
    def __init__(self, index: int, embedding: list[float]) -> None:
        self.index = index
        self.embedding = embedding


class _Resp:
    def __init__(self, data: list[_Item]) -> None:
        self.data = data


class _FakeEmbeddings:
    """伪 embeddings 端点：首元素记录原始 index，便于验证按 index 重排。"""

    def __init__(self, dim: int, *, reverse: bool = False) -> None:
        self._dim = dim
        self._reverse = reverse

    def create(self, *, model: str, input: list[str]) -> _Resp:  # noqa: A002
        items = [_Item(i, [float(i)] * self._dim) for i in range(len(input))]
        if self._reverse:
            items.reverse()
        return _Resp(items)


def _fake_embedding_client(monkeypatch, *, dim: int, reverse: bool = False) -> None:
    fake = SimpleNamespace(embeddings=_FakeEmbeddings(dim, reverse=reverse))
    monkeypatch.setattr(openai_compat, "_make_client", lambda **kw: fake)


def test_embedding_dim_mismatch_raises_with_guidance(monkeypatch) -> None:
    """模型返回维度 ≠ EMBEDDING_DIM 必须报错：静默写出错长度向量后，
    表现为"检索查得到但永远不相关"，极难定位。"""
    _fake_embedding_client(monkeypatch, dim=2048)
    emb = OpenAICompatEmbedding(base_url=_BAILIAN_URL, api_key="k", model="m", dim=1024)
    with pytest.raises(ValueError) as ei:
        emb.embed(["你好"])
    msg = str(ei.value)
    assert "2048" in msg and "1024" in msg  # 实际维度 + 配置维度
    assert "重建" in msg  # 给出可执行方向


def test_embedding_dim_match_returns_vectors(monkeypatch) -> None:
    _fake_embedding_client(monkeypatch, dim=1024)
    emb = OpenAICompatEmbedding(base_url=_BAILIAN_URL, api_key="k", model="m", dim=1024)
    out = emb.embed(["甲", "乙"])
    assert len(out) == 2
    assert len(out[0]) == 1024


def test_embedding_result_ordered_by_index(monkeypatch) -> None:
    """SDK 不保证 data 有序，打乱后必须按 index 重排，才能与输入一一对齐。"""
    _fake_embedding_client(monkeypatch, dim=4, reverse=True)
    emb = OpenAICompatEmbedding(base_url=_BAILIAN_URL, api_key="k", model="m", dim=4)
    out = emb.embed(["甲", "乙", "丙"])
    assert [v[0] for v in out] == [0.0, 1.0, 2.0]
