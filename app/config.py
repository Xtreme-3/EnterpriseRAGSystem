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
    embedding_provider: str = ""  # dashscope | zhipu | bailian | mock，空=跟随 rag_provider
    llm_provider: str = ""        # dashscope | zhipu | bailian | mock，空=跟随 rag_provider
    # 重排供应商（I1，K0 补）：留空 = 跟随 llm_provider → rag_provider（与旧行为一致）。
    # 单开这个字段的理由：embedding 与 LLM 可能分属不同家，而重排该跟**向量空间更近**的那家走，
    # 不能硬绑在 LLM 槽位上（LLM 在中转站、embedding 在百炼时，绑 LLM 会把重排打到 404）。
    rerank_provider: str = ""     # dashscope | zhipu | bailian | mock，空=跟随 llm_provider

    # DashScope（通义千问）
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    # 阿里云百炼官方（Model Studio）—— https://bailian.console.aliyun.com/
    # 与上面的 dashscope 槽位分开：本项目的 dashscope 槽位历史指向第三方中转站
    # tokenrhythm.studio，两者共用一个 base_url 会把 LLM 一起拽到百炼官方端点上。
    bailian_api_key: str = ""
    bailian_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    # 智谱 GLM
    zhipu_api_key: str = ""
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"

    # ---- 模型 ----
    embedding_model: str = "text-embedding-v3"
    llm_model: str = "qwen-plus"
    embedding_dim: int = 1024  # text-embedding-v3=1024；zhipu embedding-3=2048 时改这里
    # 生成参数（K3）：原先硬编码在 OpenAICompatLLM._params 里。低温是为了让答案贴着资料，
    # 减少编造；max_tokens 限制单次答案长度上限。
    # ⚠️ max_tokens 是**含推理 token** 的总预算：qwen3.8-flash 这类推理模型会把
    # reasoning_tokens 也算在里面（实测一问就吃 900+）。给 1024 时正文只剩 ~100 个 token，
    # finish_reason=length，答案被截在句子中间甚至为空。故默认给 2048。
    llm_temperature: float = 0.2
    llm_max_tokens: int = 2048

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
    # 真实重排模型名。百炼走原生端点（实测 gte-rerank-v2 / qwen3-rerank 可用）；
    # 其他供应商走 {base_url}/rerank（OpenAI 兼容）。
    rerank_model: str = "gte-rerank-v2"
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
