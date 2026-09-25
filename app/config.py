"""全局配置：从 .env / 环境变量读取，pydantic-settings 校验。"""
from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
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

    # 插槽级覆盖（留空 = 跟随 rag_provider）。场景：LLM 走中转站 A（只有对话模型），
    # embedding 走另一家或本地 mock。三插槽本就独立，这里只是把"可分别配置"暴露出来。
    embedding_provider: str = ""  # dashscope | zhipu | mock，空=跟随 rag_provider
    llm_provider: str = ""        # dashscope | zhipu | mock，空=跟随 rag_provider

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
    # 余弦相似度命中阈值：低于此值的切片视为无关，不计入命中来源/日志统计
    # （真实 embedding 对无关文本常有小正分，0 阈值会让噪声污染质检/溯源/问答日志）
    similarity_threshold: float = 0.1
    vector_store: str = "chroma"  # chroma | pgvector
    # 检索模式：vector（纯向量）/ hybrid（向量 + 关键词全文融合，默认）。H1 混合检索
    retrieval_mode: str = "hybrid"
    # 重排（I1）：true 启用——检索后按重排模型/词重叠重排候选，替换检索分作为命中分。
    # 默认 false（不引入额外延迟/成本，检索排序与分数完全不变）。
    rerank: bool = False
    # 真实重排模型名（DashScope gte-rerank 等，走 {base_url}/rerank）
    rerank_model: str = "gte-rerank"
    # 切块策略（H2 语义切分）：fixed（定长递归，默认，回归兜底）| structure（结构优先语义切分）
    # | structure+semantic（结构 + 句级 embedding 微调，需真实 embedding 才有意义）
    chunk_strategy: str = "fixed"
    # structure+semantic 用：相邻句子 embedding 余弦相似度低于此值视为主题断层，在断层处切块
    semantic_break_threshold: float = 0.65
    data_dir: Path = ROOT_DIR / "data"

    # ---- PostgreSQL（元数据 + 向量共库） ----
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_user: str = "postgres"
    pg_password: str = ""
    pg_database: str = "ragdb"

    # ---- 鉴权 ----
    jwt_secret: str = "change-me-in-production"
    jwt_expire_hours: int = 24

    @field_validator("chunk_strategy")
    @classmethod
    def _validate_chunk_strategy(cls, v: str) -> str:
        if v not in ("fixed", "structure", "structure+semantic"):
            raise ValueError("chunk_strategy 必须是 fixed | structure | structure+semantic")
        return v

    def model_post_init(self, _context) -> None:
        """启动时校验：JWT secret 不能使用默认值（生产部署安全要求）。"""
        if self.jwt_secret == "change-me-in-production":
            import warnings
            warnings.warn(
                "JWT_SECRET 仍为默认值 'change-me-in-production'，生产部署请设置环境变量 JWT_SECRET",
                stacklevel=2,
            )

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

    @property
    def database_url(self) -> str:
        """PostgreSQL DSN（psycopg2）。密码从 .env 读取，不进版本控制。"""
        return (
            f"postgresql://{self.pg_user}:{self.pg_password}"
            f"@{self.pg_host}:{self.pg_port}/{self.pg_database}"
        )


def get_settings() -> Settings:
    """惰性单例，避免多处 import 时重复解析。"""
    return Settings()
