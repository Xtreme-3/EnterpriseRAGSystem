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
- **测试**：pytest + httpx + TestClient，共 212 个用例

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

## 运行时装配（K2，写路由前必读）

`RagPipeline` / `IngestionPipeline` 及全部 provider 与向量库**在 lifespan 里装配一次**，
挂在 `app.state`。**路由里不要再 new 管线、不要再调 `build_llm()` 之类的工厂**：

```python
from app.api.deps import get_rag, get_ingestion

rag = get_rag(request)              # RagPipeline，含 .retriever / .vector_store / .embedding
ingest = get_ingestion(request)     # IngestionPipeline，与 rag 共用向量库与 embedding
```

- 可用：`app.state.settings` / `app.state.rag` / `app.state.ingestion` / `app.state.session_factory`
- `get_rag` / `get_ingestion` 在缺失时抛带指引的 `RuntimeError`（`TestClient` 忘记用
  `with` 触发 lifespan 时最容易遇到）
- **原因不是省内存，是省时间**：每构造一个 OpenAI client 约 **650ms**
  （httpx 建 3 个 SSLContext），重建即每请求白送这段时间
- 相关基准脚本：`scripts/k2_assembly_bench.py`

## 前端设计规范（DESIGN.md）

**动任何前端代码（`.vue` / `.css` / 组件样式）之前，先读 [`DESIGN.md`](DESIGN.md)。**

- `DESIGN.md` 定义「界面长什么样」，本文件定义「怎么搭」——两者分工不同，都要遵守
- 它是唯一的设计 token 来源：颜色 / 字号 / 圆角 / 间距 / 组件形态都在里面，**不要在页面里臆造新值**
- 完整的设计理念与历史决策见 `docs/design-guide.md`（去 AI 味硬规则）与 `docs/design-manifest.md`（逐页改造清单）
- DESIGN.md 里 **十、Known Gaps** 列出的 9 项是「已知但尚未落地」的偏差，不要按现状把它们当正确做法去复制

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
| 真流式生成 K1 / 管线单例 K2（阶段五） | ✅ |

**212 测试全绿**（99 通过 / 113 跳过，跳过项需 PostgreSQL 或真实 API Key）。未排期项见 `docs/roadmap.md` 末尾（反馈日志、数据看板、异步摄取队列、向量库可插拔、多模型配置、审计日志、SSO/LDAP、更多格式、RPA 集成）。
