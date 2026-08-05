# Enterprise RAG System

企业级 RAG 系统：对内网/私有知识库文档做语义检索与智能问答。

**阶段一（当前）**：可独立运行的 headless 核心引擎 —— 解析 → 切片 → 向量化 → 存储 → 检索 → 生成，CLI 一条命令跑通。

## 技术栈

- Python 3.11+ / FastAPI（阶段二）/ SQLAlchemy + SQLite（元数据）/ ChromaDB（向量）
- 模型供应商：通义千问（DashScope）或 智谱 GLM，OpenAI 兼容接口，可插拔

## 快速开始

```bash
# 1. 建虚拟环境并装依赖
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -e ".[dev]"

# 2. 配置（可选；不配置则用 mock 供应商离线运行）
copy .env.example .env            # Windows；Linux/macOS: cp .env.example .env
#    在 .env 中填 DASHSCOPE_API_KEY 或 ZHIPU_API_KEY

# 3. 跑通全链路
python scripts/demo.py
```

demo 会 ingest 内置示例文档并针对其内容提问，打印答案与来源引用。

## 目录结构

```
app/
├── config.py          # 配置（.env）
├── providers/         # 模型供应商抽象 + 实现（openai 兼容 / mock）
├── ingestion/         # 文档解析、切片、入库
├── storage/           # SQLite 元数据 + ChromaDB 向量库
└── rag/               # 检索 → 重排 → 生成
scripts/demo.py        # 冒烟测试
tests/                 # 离线单测（mock 供应商，无需网络/密钥）
```

## 配置项

见 `.env.example`。核心项：`RAG_PROVIDER`（dashscope / zhipu / mock）、
`EMBEDDING_MODEL`、`LLM_MODEL`、`CHUNK_SIZE`、`TOP_K`。

## Roadmap

- [x] 阶段一：核心引擎（解析/切片/向量化/检索/生成）
- [ ] 阶段二：FastAPI 服务（知识库/文档/对话流式/鉴权）
- [ ] 阶段三：Vue3 + Element Plus 前端
