"""K2 装配开销基准：量化「每请求重建 provider」到底白送多少毫秒。

跑法（项目根目录）::

    .venv\\Scripts\\python.exe scripts\\k2_assembly_bench.py

背景
----
改造前 `_build_rag()` / `_build_ingest()` 在每个请求里都执行
`build_llm()` / `build_embedding()` / `build_vector_store()`。
改造后在 lifespan 里只装配一次，请求期直接取 `app.state.rag`。

本脚本只测**构造**开销（本地、不出网）。构造里真正贵的是 httpx 为
每个 client 建 SSLContext —— 会调用 ``ssl.SSLContext.load_verify_locations``
解析整个 CA bundle，单次 ~200ms 量级；而 httpx 在 ``trust_env=True``（默认）
下还会为探测到的系统代理各建一个传输，于是**一个 client 要建 3 个 SSLContext**。

这也是为什么「省掉重建」不只是省内存，而是省下每请求几百毫秒。

注意
----
真实生产上第一次请求还要额外付一次 TLS 握手（DNS + TCP + TLS），那部分
必须连真实模型端点才能测到，本脚本不覆盖。
"""
from __future__ import annotations

import statistics
import time
from collections.abc import Callable

from app.config import get_settings
from app.providers.factory import build_embedding, build_llm, build_reranker
from app.storage.vector_store import build_vector_store

_ROUNDS = 12
_SIMULATED_REQUESTS = 10


def median_ms(fn: Callable[[], object], rounds: int = _ROUNDS) -> float:
    samples = []
    for _ in range(rounds):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def main() -> None:
    settings = get_settings()
    builders: dict[str, Callable[[], object]] = {
        "build_llm": lambda: build_llm(settings),
        "build_embedding": lambda: build_embedding(settings),
        "build_reranker": lambda: build_reranker(settings),
        "build_vector_store": lambda: build_vector_store(settings),
    }

    print(f"provider={settings.rag_provider}  vector_store={settings.vector_store}")
    print(f"每项取 {_ROUNDS} 次的中位耗时\n")

    per_build: dict[str, float] = {}
    for name, fn in builders.items():
        per_build[name] = median_ms(fn)
        print(f"  {name:<20} : {per_build[name]:8.2f} ms")

    per_request = sum(per_build.values())
    print(f"\n  {'单次请求固定开销':<20} : {per_request:8.2f} ms")

    before = per_request * _SIMULATED_REQUESTS
    after = per_request  # 启动时装配一次
    print(f"\n改造前：{_SIMULATED_REQUESTS} 次请求 = {before:9.1f} ms")
    print(f"改造后：{_SIMULATED_REQUESTS} 次请求 = {after:9.1f} ms  （仅启动付一次）")
    if per_request > 0:
        print(f"省下  ：{before - after:9.1f} ms  ≈ 每请求 {per_request:.0f} ms")


if __name__ == "__main__":
    main()
