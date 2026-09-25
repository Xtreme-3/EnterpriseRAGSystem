# EnterpriseRAGSystem — Agent 协作说明

> 本文件是给 AI 编码助手（Claude Code / Cursor / Codex 等）看的项目上下文。
> 人类读者请先看 [README.md](README.md)。

## 技术栈

- **后端**：Python 3.11+ / FastAPI / SQLAlchemy 2.0 / Pydantic v2
- **前端**：Vue3 + Vite + Element Plus + Pinia + vue-router
- **向量库**：ChromaDB（默认，开箱即用）/ PostgreSQL + pgvector（可切换）
- **元数据**：SQLite（默认）/ PostgreSQL
- **模型**：通义千问 DashScope / 智谱 GLM / mock（离线可测），OpenAI 兼容接口，可插拔
- **鉴权**：bcrypt + JWT（python-jose, HS256）
- **测试**：pytest + httpx + TestClient，共 187 个用例

## 目录结构

```
app/
├── main.py              # FastAPI 入口，lifespan，全局异常处理
├── config.py            # pydantic-settings 读 .env
├── core/models.py       # User / KnowledgeBase / KnowledgeBaseMember /
│                        # Document / Conversation / ChatMessage / QaLog
├── storage/
│   ├── db.py            # init_db、get_db 会话
│   └── vector_store.py  # VectorStore 抽象（chroma / pgvector 双实现）
├── api/                 # auth / kbs / documents / chat /
│                        # inspect / diagnostics / qa-logs / doc-health
├── ingestion/           # parsers 解析、chunker 切片、pipeline 编排
├── providers/           # LLM / Embedding / Rerank 三套供应商抽象
└── rag/                 # retriever → reranker → query_rewriter → generator
frontend/src/            # Vue3 前端，10 个页面
docs/                    # 架构图 / 需求卡片 / 进度日志 / 踩坑日志 / 路线图
```

## 开发约定（积木式开发）

1. 动工前在 `docs/requirements/<id>-<name>.md` 写需求卡片
2. 实现到验收标准
3. 跑测试：`python -m pytest tests/ -v --tb=short`
4. 更新 `docs/progress-log.md` 与 `docs/roadmap.md`
5. **解释用中文，代码 / 命令 / 路径用英文**

## API 约定

- 统一前缀 `/api/...`，鉴权路由 `/api/auth/...`
- 共享依赖放 `app/api/deps.py`（`get_db`、`get_current_user`）
- 错误统一走 `app.main.unhandled_exception`，返回 JSON
- 响应模型用 Pydantic `ConfigDict(from_attributes=True)` 承接 ORM 对象

## 数据库

- 默认：SQLite，自动建在 `data/`
- 可选：PostgreSQL 17 + pgvector（Docker 镜像 `pgvector/pgvector:pg17`）
- 密码哈希：bcrypt，**哈希前截断到 72 字节**
- pgvector 建表用幂等模式：`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`，兼容旧库

## 测试

- 测试 fixture 钉死 chroma，无需 PostgreSQL 即可全量跑
- pgvector 相关用例单独标记，连不上自动跳过
- 统一用 `TestClient(app, raise_server_exceptions=False)`
- 清理测试数据走原生 psycopg2（规避 Python 3.13 上 SQLAlchemy immutabledict 的问题）

## 环境

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"        # 后端 + 测试依赖
pip install -e ".[api]"        # 需要 API 服务时
copy .env.example .env         # 填 DASHSCOPE_API_KEY 或 ZHIPU_API_KEY
python scripts/demo.py         # 离线冒烟（mock 供应商）
```

## 容器化

- `Dockerfile`：API 镜像。依赖从 `pyproject.toml` 抽取后安装（pyproject 仍是唯一依赖来源），
  因此只改 `app/` 不会让依赖层缓存失效
- `frontend/Dockerfile`：node 构建 SPA → nginx 托管并反代 `/api`
- `docker-compose.yml`：`web` + `api` + `db(pgvector/pgvector:pg17)`；默认
  `VECTOR_STORE=pgvector`（元数据与向量共库，不再是 SQLite + ChromaDB）+ `RAG_PROVIDER=mock`
- **易踩的两个点**：①健康检查端点是 `/health`，**不带** `/api` 前缀，nginx 里需单独配 location；
  ②前端 axios `baseURL` 是相对路径 `/api`，走同源反代即可，后端**没有也不需要** CORS 配置
- SSE 流式问答在 nginx 里必须 `proxy_buffering off`，否则答案会被缓冲到最后一次性吐出

## 当前状态

**V1 全部完成**，进阶功能已完成 G1–G5 / H1 / H2 / I1 / I2 / J1 / J2。

| 模块 | 状态 |
|---|---|
| 核心引擎 A1–A9 | ✅ |
| 存储切换 A10（pgvector） | ✅ |
| 后端 API B1–B6 | ✅ |
| 前端 F1–F5 | ✅ |
| 质检 / 诊断 / 一致性 / 问答日志 / 文档健康 G1–G5 | ✅ |
| 混合检索 H1 / 语义切分 H2 | ✅ |
| 重排序 Rerank I1 / RBAC 权限 I2 | ✅ |
| 多轮对话 J1 / 会话持久化 J2 | ✅ |

**187 测试全绿。** 未排期项见 `docs/roadmap.md` 末尾（反馈日志、数据看板、异步摄取队列、向量库可插拔、多模型配置、审计日志、SSO/LDAP、更多格式、RPA 集成）。
