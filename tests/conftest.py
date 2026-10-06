"""全局测试夹具（K0 补）。

**测试不得依赖开发者本机的 `.env`。**

K0 把 `.env` 的 embedding 从 mock 切到阿里云百炼官方后，这个缺口立刻显形：

1. **单测会真实联网**：`build_embedding()` 直接打线上 API，跑一次测试就消耗真实额度；
2. **向量维度漂移**：测试用例按 mock 的 64 维建集合，写入真实模型的 1024 维 → 一批用例
   集体报 `chromadb.errors.InvalidArgumentError: Collection expecting embedding with
   dimension of 64, got 1024`。

环境变量优先级高于 `.env` 文件，所以这里 `setenv` 即可把供应商钉死为 mock，
让 `python -m pytest tests/` 在任何 `.env` 状态下都**离线、确定、可重复**。

⚠️ **夹具必须是 session 级**（2026-10-06 修正）。原先是函数级，看起来也能用 ——
因为在"PG 不可达 → 全部 skip"的默认状态下确实看不出来。但 pytest 是**按作用域从大到小
建夹具**的：一批 PG-gated 用例用的是模块级 `client`，而 `TestClient(app)` 一进 lifespan
就把管线建好了（`app.main` 的 lifespan 里 `build_llm()` / `build_embedding()` 会读 Settings），
此时函数级的 setenv **还没执行** → 管线绑的是 `.env` 里的真实供应商。
后果是这批用例悄悄跑在真实 LLM / 百炼 embedding 上：出网、烧额度，且断言按 mock 语义写的
（`rewritten_query` 期望拼接式兜底、按 64 维造向量、检索只命中一份文档）全部落空。
改成 session 级后，它在所有模块级夹具之前执行。

注意：`LLM_PROVIDER` / `RERANK_PROVIDER` 钉成**空串**而不是 `mock`。

`tests/test_rerank.py` 有几条用例专门验证"真实供应商装配"
（`Settings(_env_file=None, rag_provider="dashscope", rerank=True)`），
它们靠 `llm_provider or rag_provider` 的兜底链解析到 dashscope。
pydantic-settings 里**显式传参的优先级高于环境变量**，所以钉成空串既挡住 `.env` 的污染，
又保留原兜底语义；显式传 `rag_provider="dashscope"` 的用例也不受影响。
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

#: 需要钉死的插槽。改这里时同步看上面 docstring 里的两条约束。
_PINNED_ENV = {
    "RAG_PROVIDER": "mock",
    "EMBEDDING_PROVIDER": "mock",
    "LLM_PROVIDER": "",
    "RERANK_PROVIDER": "",
    "RERANK": "false",
}


@pytest.fixture(scope="session", autouse=True)
def _pin_offline_providers() -> Iterator[None]:
    """把所有会出网的插槽钉到 mock，并关掉重排。

    session 级 + `pytest.MonkeyPatch` 实例（不能用 `monkeypatch` 夹具，它是函数级的）；
    结束时 `undo()` 把 `os.environ` 还原，避免污染同进程内后续的构造。
    """
    mp = pytest.MonkeyPatch()
    for key, value in _PINNED_ENV.items():
        mp.setenv(key, value)
    yield
    mp.undo()
