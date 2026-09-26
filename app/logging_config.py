"""日志配置（K8-1）：统一 root、级别可配、每条日志带 request_id。

### 为什么改成"配 root"而不是只配 ``"app"``

旧实现（`app/main.py:33-39`）只配了一个 named logger ``"app"``，且 ``propagate=False``：

1. **``setLevel(INFO)`` 硬编码在源码里** —— 想开 DEBUG 只能改代码重启，于是
   "检索零命中""重排退化"这类只有 DEBUG 才该说的细节**永久静默**，排查时无路可走；
2. **第三方 logger 完全不受治理**：uvicorn / httpx / chromadb / sqlalchemy 的 INFO 静默丢弃，
   WARNING 落到 ``logging.lastResort``（无时间戳、无级别、无 logger 名），全进程格式不统一；
3. **``propagate=False`` 让 root 上的 handler 收不到 app 日志** —— 包括 pytest 的 `caplog`，
   于是"验证某条日志是否被打印"这件事在本仓库根本写不出来。

现在：root 持唯一 handler，``app`` 向 root 传播，级别由 ``LOG_LEVEL`` 配置。

### 为什么用 ``LogRecordFactory`` 注入 request_id，而不是 handler 上的 Filter

Filter 只作用于**挂了它的那个 handler**。而我们要的是**每一条**记录都带这个字段 ——
不管它最终被 stderr handler、测试的 caplog、还是将来接的日志平台收走。
挂在 factory 上是唯一能覆盖全部记录的位置，否则跨 handler 的关联链会断。
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(request_id)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
NO_REQUEST_ID = "-"

# 打在这个 handler 上的标记：重复装配时据此认出"自己的"handler，避免叠加
MANAGED_FLAG = "_rag_managed"
APP_LOGGER_NAME = "app"

VALID_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# 当前请求 id。只在请求上下文内有值（中间件设置），启动/后台任务读到 "-"。
# 定义在 logging 模块而不是 middleware：中间件依赖 starlette，而日志需要保持零依赖，
# 反方向 import 会让日志配置被 web 框架绑架。
request_id_ctx: ContextVar[str] = ContextVar("request_id", default=NO_REQUEST_ID)

_factory_installed = False


def current_request_id() -> str:
    """当前请求的 id；不在请求上下文内返回 ``"-"``。"""
    return request_id_ctx.get()


@contextmanager
def request_id_scope(request_id: str | None) -> Iterator[None]:
    """临时把日志上下文切到指定 ``request_id``。

    存在的唯一理由：**未处理异常处理器跑在中间件之外**。异常先沿 ASGI 栈冒泡，
    ``RequestIdMiddleware`` 的 ``finally``（重置 contextvar）先执行，之后最外层的
    ``ServerErrorMiddleware`` 才调用 ``@app.exception_handler(Exception)``。
    结果就是最关键的那条 500 日志反而丢了 request_id。处理器必须用它从
    ``request.state`` 里取回的 id 重新建立上下文。
    """
    if request_id is None:
        yield
        return
    token = request_id_ctx.set(request_id)
    try:
        yield
    finally:
        request_id_ctx.reset(token)


def _install_record_factory() -> None:
    """给每条 LogRecord 补 ``request_id`` 字段（幂等）。"""
    global _factory_installed
    if _factory_installed:
        return

    base_factory = logging.getLogRecordFactory()

    def _factory(*args, **kwargs):
        record = base_factory(*args, **kwargs)
        record.request_id = request_id_ctx.get()
        return record

    logging.setLogRecordFactory(_factory)
    _factory_installed = True


def _managed_handler() -> logging.StreamHandler:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
    setattr(handler, MANAGED_FLAG, True)
    return handler


def _clear_managed(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        if getattr(handler, MANAGED_FLAG, False):
            logger.removeHandler(handler)


def resolve_level(level: str | int) -> int:
    """名字 → 级别常量。未知名字退回 INFO（启动期已由 Settings 校验拦下，这里只兜底）。"""
    if isinstance(level, int):
        return level
    resolved = getattr(logging, str(level or "").strip().upper(), None)
    return resolved if isinstance(resolved, int) else logging.INFO


def setup_logging(level: str | int = "INFO") -> logging.Logger:
    """装配进程日志。**幂等**：重复调用只更新级别，不叠加 handler。

    lifespan 重入、测试里多次 import `app.main` 都会重复触发，叠加 handler 会让
    同一行日志打印多遍。
    """
    resolved = resolve_level(level)
    _install_record_factory()

    root = logging.getLogger()
    _clear_managed(root)
    root.addHandler(_managed_handler())
    root.setLevel(resolved)

    app_logger = logging.getLogger(APP_LOGGER_NAME)
    _clear_managed(app_logger)
    app_logger.setLevel(resolved)
    app_logger.propagate = True
    return app_logger
