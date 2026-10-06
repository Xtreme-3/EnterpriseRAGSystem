"""FastAPI 应用入口：统一基座（B1 骨架）。

职责：
- 启动时初始化数据库（元数据建表，pgvector / 本地两模式）
- 提供 `/health` 健康检查与 `/` 服务信息
- 未处理异常统一 JSON 响应（完整堆栈只进日志，不返回客户端）

业务接口由后续积木（B2–B6）挂载到 `/api`。
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.logging_config import current_request_id, request_id_scope, setup_logging
from app.middleware import REQUEST_ID_HEADER, RequestIdMiddleware
from app.storage.db import init_db

APP_NAME = "EnterpriseRAG API"
APP_VERSION = "0.1.0"

# Windows 控制台默认 GBK：重配置为 UTF-8，避免中英文混合日志乱码/崩溃
# （与 scripts/demo.py 一致；若异常处理器自身日志崩溃会掩盖真实错误）
if sys.stderr is not None:
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 应用日志（K8）：root 持唯一 handler，`app.*` 向 root 传播，级别由 LOG_LEVEL 配置。
# 旧写法把 setLevel(INFO) 硬编码在这里且 propagate=False —— DEBUG 永久静默、
# 第三方 logger 不受治理、root 上的 handler（含测试捕获）收不到 app 日志。
# 详见 app/logging_config.py 的模块注释。
setup_logging(get_settings().log_level)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库并**装配一次** RAG / 摄取管线（K2）。

    构造失败即启动失败 —— 早暴露，避免带病启动。默认 mock + chroma 路径
    在无 API Key、无 PostgreSQL 时也能正常启动（engine 是惰性的）。
    """
    from concurrent.futures import ThreadPoolExecutor

    settings = get_settings()
    _, session_factory = init_db(settings)
    app.state.settings = settings
    app.state.session_factory = session_factory

    from app.ingestion.pipeline import IngestionPipeline
    from app.providers.factory import build_embedding, build_llm, build_reranker
    from app.rag.pipeline import RagPipeline
    from app.storage.vector_store import build_vector_store

    # 向量库与 embedding 只建一份：问答与摄取共用（同一 engine、同一 httpx 池）
    vector_store = build_vector_store(settings)
    embedding = build_embedding(settings)

    rag = RagPipeline(
        settings=settings,
        session_factory=session_factory,
        vector_store=vector_store,
        embedding=embedding,
        llm=build_llm(settings),
        reranker=build_reranker(settings),
    )
    app.state.rag = rag

    # K4：后台摄取执行器。选 ThreadPoolExecutor 而不是 FastAPI BackgroundTasks ——
    # 前者能设**并发上限**（后者每个请求各起一个，同时上传 10 份就会把 embedding
    # 接口打爆，反而全线变慢），也能在退出时统一收尾。
    executor = ThreadPoolExecutor(
        max_workers=settings.ingest_workers, thread_name_prefix="ingest"
    )
    app.state.ingestion_executor = executor

    ingestion = IngestionPipeline(
        settings=settings,
        session_factory=session_factory,
        vector_store=vector_store,
        embedding=embedding,
        executor=executor,
        # K6 联动：语料变更（job 落终态）才失效问答缓存，不是上传那一刻
        on_corpus_changed=rag.cache.invalidate_kb,
    )
    app.state.ingestion = ingestion

    # K4 启动自愈：执行器随进程消失，上次停机时卡住的 job 永远不会再被推进 ——
    # 显式置 failed，否则前端一直转圈且没有任何出口。
    stale = ingestion.fail_stale_jobs()
    if stale:
        logger.warning("启动自愈：%s 个摄取任务在上次停机时未完成，已置为 failed", stale)

    logger.info("数据库就绪（vector_store=%s）；RAG 管线已装配一次", settings.vector_store)
    try:
        yield
    finally:
        # 等正在跑的摄取收尾再释放线程：不等的话，测试里线程会跨用例泄漏，
        # 生产里重启会把任务腰斩在写向量的中途（留下半截数据）。
        executor.shutdown(wait=True)


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="企业级 RAG 系统 API —— 阶段二（B1 骨架）",
    lifespan=lifespan,
)

# K8-2：所有响应带 X-Request-ID（透传或生成），并把它注入日志上下文
app.add_middleware(RequestIdMiddleware)


@app.get("/health")
def health() -> dict[str, str]:
    """健康检查：进程存活即 ok（数据库就绪见启动日志）。"""
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    """服务基本信息。"""
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "docs": "/docs",
        "health": "/health",
    }


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    """未处理异常 → 统一 JSON 500；完整堆栈写日志，客户端只见通用信息 + request_id。"""
    # request.state.request_id 由 RequestIdMiddleware 从 scope 上挂进来。
    # 不能直接读 contextvar：异常是"先冒泡出中间件、后交给本处理器"，
    # 中间件的 finally 已经把上下文重置了（详见 request_id_scope 的注释）。
    request_id = getattr(request.state, "request_id", None) or current_request_id()
    with request_id_scope(request_id):
        logger.exception("未处理异常 %s %s: %s", request.method, request.url.path, exc)
    return _internal_error_response(request_id)


def _internal_error_response(request_id: str) -> JSONResponse:
    """未处理异常的响应体。

    带 ``request_id`` 是 K8-2 的关键：用户报障时能提供它，运维据此在日志里
    一条 ``grep`` 定位到该请求的全部日志行；只给「服务器内部错误」的话，
    双方都只能靠时间戳猜。

    **响应头必须在这里自己补**，不能指望 ``RequestIdMiddleware``：
    ``ServerErrorMiddleware`` 永远在最外层，它捕获异常后用自己的 ``send`` 发出 500，
    绕过了我们这层 ``send`` 包装 —— 由中间件统一加头的那条路径覆盖不到未处理异常。
    """
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误", "request_id": request_id},
        headers={REQUEST_ID_HEADER: request_id},
    )


# ---- 挂载业务路由 ----
from app.api.auth import router as auth_router  # noqa: E402
from app.api.config import router as config_router  # noqa: E402
from app.api.kbs import router as kbs_router  # noqa: E402
from app.api.documents import router as documents_router  # noqa: E402
from app.api.chat import router as chat_router  # noqa: E402
from app.api.inspect import router as inspect_router  # noqa: E402
from app.api.diagnostics import router as diagnostics_router  # noqa: E402
from app.api.dashboard import router as dashboard_router  # noqa: E402
from app.api.doc_health import router as doc_health_router  # noqa: E402

app.include_router(auth_router)
app.include_router(config_router)
app.include_router(kbs_router)
app.include_router(documents_router)
app.include_router(chat_router)
app.include_router(inspect_router)
app.include_router(diagnostics_router)
app.include_router(dashboard_router)
app.include_router(doc_health_router)
