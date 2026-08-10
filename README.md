# Enterprise RAG System

企业级 RAG 系统：对内网/私有知识库文档做语义检索与智能问答。

## 🎯 V1 目标（基础功能）

第一版只做以下**基础功能**（拆成小积木逐步搭建，进度见 `docs/progress-log.md`）：

1. **用户登录** —— 账号密码 + JWT 会话
2. **多知识库隔离** —— 创建/列表/删除知识库，数据按库物理隔离
3. **文档上传 + 向量化** —— PDF / TXT / MD 上传 → 解析 → 切片 → 向量化 → 入库
4. **文档管理** —— 文档列表 / 状态 / 删除
5. **RAG 流式问答 + 溯源** —— 提问 → 检索 → 流式生成 → 答案附带来源引用

## 当前进度

- ✅ **阶段一（已完成）**：核心引擎 —— 解析/切片/向量化/检索/生成，CLI 冒烟通过
- ✅ **阶段二（已完成）**：FastAPI API 服务 —— 鉴权/知识库CRUD/文档上传/流式问答
- ✅ **阶段三（已完成）**：Vue3 + Element Plus 前端 —— 登录/知识库/文档/对话全流程

详细进度与需求卡片见 [docs](docs/README.md)，架构图见 [docs/architecture.md](docs/architecture.md)，**完整积木地图见 [docs/roadmap.md](docs/roadmap.md)**。

## 技术栈

- 后端：Python 3.11+ / FastAPI / SQLAlchemy + SQLite（元数据）/ ChromaDB（向量）
- 前端：Vue3 + Vite + Element Plus（阶段三）
- 模型：通义千问（DashScope）/ 智谱 GLM，OpenAI 兼容接口，可插拔；mock 支持离线运行

## 快速开始

```bash
# 1. 建虚拟环境并装依赖
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -e ".[dev]"

# 2. 配置（可选；不配置则用 mock 供应商离线运行）
copy .env.example .env            # Windows；Linux/macOS: cp .env.example .env
#    在 .env 中填 DASHSCOPE_API_KEY 或 ZHIPU_API_KEY

# 3. 跑通核心引擎
python scripts/demo.py
```

## 目录结构

```
app/
├── config.py          # 配置（.env）
├── providers/         # 模型供应商抽象 + 实现（openai 兼容 / mock）
├── ingestion/         # 文档解析、切片、入库
├── storage/           # SQLite 元数据 + ChromaDB 向量库
└── rag/               # 检索 → 重排 → 生成
docs/                  # 架构图 / 需求卡片 / 进度日志
scripts/demo.py        # 冒烟测试
tests/                 # 离线单测（mock 供应商，无需网络/密钥）
```

## 配置项

见 `.env.example`。核心项：`RAG_PROVIDER`（dashscope / zhipu / mock）、
`EMBEDDING_MODEL`、`LLM_MODEL`、`CHUNK_SIZE`、`TOP_K`、`RETRIEVAL_MODE`（vector / hybrid）。

## 🗺️ 迭代路线图

> 每个功能都是独立积木，动工前在 `docs/requirements/` 写需求卡片，完成后更新进度日志。

### V1 基础功能（按顺序搭建）

| # | 功能积木 | 说明 |
|---|---|---|
| 1 | 用户登录 | 账号密码 + JWT 会话 |
| 2 | 多知识库隔离 | 知识库 CRUD，数据按库隔离 |
| 3 | 文档上传向量化 | PDF/TXT/MD → 解析→切片→向量化→入库 |
| 4 | 文档管理 | 列表 / 状态 / 删除 |
| 5 | 流式问答 + 溯源 | 检索 → 流式生成 → 引用来源 |

### 进阶功能（后续迭代，按优先级）

| 优先级 | 功能 | 说明 |
|---|---|---|
| ⭐⭐⭐ | RBAC 细粒度权限 | 角色（管理员/编辑/只读）+ 按知识库授权 |
| ⭐⭐⭐ | 混合检索 | 关键词（tsvector / 词频）+ 向量双路融合，提升精确词/型号/代码召回（**H1 已完成**） |
| ⭐⭐⭐ | 重排序 Rerank | 接入 gte-rerank / 智谱 rerank，提升精度 |
| ⭐⭐ | 多轮对话记忆 | 会话历史作为上下文 |
| ⭐⭐ | 点赞/点踩 + 反馈日志 | 收集用户反馈 → 难例分析 |
| ⭐⭐ | 数据看板 | 文档量 / 提问量 / 点赞率 / 检索命中分析 |
| ⭐⭐ | **管理后台** | 用户管理 / 系统配置 / 审计日志查询 / 知识库全局管理 |
| ⭐⭐ | 文档增量更新 | 重新向量化、版本管理 |
| ⭐ | 异步摄取队列 | 重试、断点续传、大规模批量 |
| ⭐ | 向量库可插拔 | 部署期换 pgvector / LanceDB |
| ⭐ | 多模型配置 | 模型切换、路由、按库指定模型 |
| ⭐ | 审计日志 | 操作留痕，合规 |
| ⭐ | SSO / LDAP 集成 | 企业统一认证 |
| ⭐ | RPA 集成 | Playwright 浏览器自动化 + RAG 智能决策，自动操作电商/客服后台 |
| ⭐ | 更多文档格式 | Excel / PPT / 图片 OCR |

## 文档

- [架构图](docs/architecture.md)
- [需求卡片模板](docs/requirements/TEMPLATE.md)
- [进度日志](docs/progress-log.md)
