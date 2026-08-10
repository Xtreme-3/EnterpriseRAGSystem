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
from app.storage.db import init_db

APP_NAME = "EnterpriseRAG API"
APP_VERSION = "0.1.0"

# Windows 控制台默认 GBK：重配置为 UTF-8，避免中英文混合日志乱码/崩溃
# （与 scripts/demo.py 一致；若异常处理器自身日志崩溃会掩盖真实错误）
if sys.stderr is not None:
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
if sys.stdout is not None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 应用日志：只配置 app 记录器，不碰 root / uvicorn 的配置，避免双重输出
logger = logging.getLogger("app")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.INFO)
logger.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库（建元数据表）；失败快速失败，避免带病启动。"""
    settings = get_settings()
    _, session_factory = init_db(settings)
    app.state.session_factory = session_factory
    logger.info("数据库就绪（vector_store=%s）", settings.vector_store)
    yield


app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="企业级 RAG 系统 API —— 阶段二（B1 骨架）",
    lifespan=lifespan,
)


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
    """未处理异常 → 统一 JSON 500；完整堆栈写日志，客户端只见通用信息。"""
    logger.exception("未处理异常 %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误"})


# ---- 挂载业务路由 ----
from app.api.auth import router as auth_router  # noqa: E402
from app.api.kbs import router as kbs_router  # noqa: E402
from app.api.documents import router as documents_router  # noqa: E402
from app.api.chat import router as chat_router  # noqa: E402
from app.api.inspect import router as inspect_router  # noqa: E402
from app.api.diagnostics import router as diagnostics_router  # noqa: E402
from app.api.doc_health import router as doc_health_router  # noqa: E402

app.include_router(auth_router)
app.include_router(kbs_router)
app.include_router(documents_router)
app.include_router(chat_router)
app.include_router(inspect_router)
app.include_router(diagnostics_router)
app.include_router(doc_health_router)
