"""全局配置：从 .env / 环境变量读取，pydantic-settings 校验。"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（app/config.py 的上上级）
ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 模型供应商 ----
    # 默认 mock：无需 API Key 即可离线跑通全链路；配 .env 后改 dashscope / zhipu
    rag_provider: str = "mock"  # dashscope | zhipu | mock

    # DashScope（通义千问）
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    # 智谱 GLM
    zhipu_api_key: str = ""
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"

    # ---- 模型 ----
    embedding_model: str = "text-embedding-v3"
    llm_model: str = "qwen-plus"
    embedding_dim: int = 1024  # text-embedding-v3=1024；zhipu embedding-3=2048 时改这里

    # ---- RAG 参数 ----
    chunk_size: int = 800
    chunk_overlap: int = 120
    top_k: int = 5
    vector_store: str = "chroma"  # chroma | lancedb | sqlite-vec（预留）
    data_dir: Path = ROOT_DIR / "data"

    # ---- 路径派生 ----
    @property
    def db_path(self) -> Path:
        return self.data_dir / "rag.db"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def sqlite_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"


def get_settings() -> Settings:
    """惰性单例，避免多处 import 时重复解析。"""
    return Settings()
