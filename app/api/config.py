"""对话页参数配置（K7/K5）：把模型清单、重排可用性、反馈原因标签暴露给前端。

模型清单来自 ``Settings.llm_model_options``（逗号分隔，``llm_model`` 恒在首位），
反馈原因标签来自 ``app.api.chat.FEEDBACK_REASONS``（前后端唯一来源，防止两处漂移）。
只读、需登录：这些值本身不敏感，但保持与其它 /api 路由一致的鉴权约定。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api.chat import FEEDBACK_REASONS
from app.api.deps import get_current_user
from app.core.models import User
from app.ingestion.parsers import SUPPORTED_EXTS

router = APIRouter(prefix="/api/config", tags=["config"])


class ChatOptionsOut(BaseModel):
    models: list[str]
    rerank: bool
    top_k_default: int
    feedback_reasons: list[str] = []


class UploadConfigOut(BaseModel):
    supported_exts: list[str]


@router.get("/chat", response_model=ChatOptionsOut)
def chat_options(
    request: Request = None,  # type: ignore[assignment]
    current_user: User = Depends(get_current_user),
) -> ChatOptionsOut:
    """对话页可调参数与配置的服务端下发（K7 / K5）。

    读 ``app.state.settings``（lifespan 装配，K2 约定；TestClient 需用 ``with``
    触发 lifespan），与 chat 路由做 model 校验用的是**同一份**配置，不会漂移。
    """
    settings = request.app.state.settings
    return ChatOptionsOut(
        models=settings.llm_model_choices,
        rerank=settings.rerank,
        top_k_default=settings.top_k,
        feedback_reasons=list(FEEDBACK_REASONS),
    )


@router.get("/upload", response_model=UploadConfigOut)
def upload_config(current_user: User = Depends(get_current_user)) -> UploadConfigOut:
    """上传能力配置（K10）：受支持的扩展名由后端注册表下发。

    此前 KbsList / DocList 各自硬编码一份扩展名列表——后端注册表加 `.xlsx`
    的同一天就会产生三处漂移。前端上传口一律以本端点为准。
    """
    return UploadConfigOut(supported_exts=sorted(SUPPORTED_EXTS))
