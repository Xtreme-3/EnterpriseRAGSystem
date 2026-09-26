"""全局测试夹具（K0 补）。

**测试不得依赖开发者本机的 `.env`。**

K0 把 `.env` 的 embedding 从 mock 切到阿里云百炼官方后，这个缺口立刻显形：

1. **单测会真实联网**：`build_embedding()` 直接打线上 API，跑一次测试就消耗真实额度；
2. **向量维度漂移**：测试用例按 mock 的 64 维建集合，写入真实模型的 1024 维 → 一批用例
   集体报 `chromadb.errors.InvalidArgumentError: Collection expecting embedding with
   dimension of 64, got 1024`。

环境变量优先级高于 `.env` 文件，所以这里 `setenv` 即可把供应商钉死为 mock，
让 `python -m pytest tests/` 在任何 `.env` 状态下都**离线、确定、可重复**。

注意：`LLM_PROVIDER` / `RERANK_PROVIDER` 钉成**空串**而不是 `mock`。

`tests/test_rerank.py` 有几条用例专门验证"真实供应商装配"
（`Settings(_env_file=None, rag_provider="dashscope", rerank=True)`），
它们靠 `llm_provider or rag_provider` 的兜底链解析到 dashscope。若把这两个字段设成
`mock`，兜底链会在第一步就命中 mock，这些用例会**静默失去覆盖**；
设成空串则既挡住 `.env` 的污染，又保留原兜底语义。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _pin_offline_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """把所有会出网的插槽钉到 mock，并关掉重排。"""
    monkeypatch.setenv("RAG_PROVIDER", "mock")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("LLM_PROVIDER", "")
    monkeypatch.setenv("RERANK_PROVIDER", "")
    monkeypatch.setenv("RERANK", "false")
