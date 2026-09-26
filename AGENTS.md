# EnterpriseRAGSystem — Agent 协作说明

> 本文件是给 AI 编码助手（Claude Code / Cursor / Codex 等）看的项目上下文。
> 人类读者请先看 [README.md](README.md)。

## 技术栈

- **后端**：Python 3.11+ / FastAPI / SQLAlchemy 2.0 / Pydantic v2
- **前端**：Vue3 + Vite + Element Plus + Pinia + vue-router
- **向量库**：ChromaDB（默认，开箱即用）/ PostgreSQL + pgvector（可切换）
- **元数据**：SQLite（默认）/ PostgreSQL
- **模型**：阿里云百炼官方 / 智谱 GLM / 第三方中转站（`dashscope` 槽位）/ mock（离线可测），
  OpenAI 兼容接口；LLM / Embedding / Rerank **三插槽可分别指向不同供应商**
- **鉴权**：bcrypt + JWT（python-jose, HS256）
- **测试**：pytest + httpx + TestClient，共 273 个用例

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

## 模型供应商插槽（K0，写 provider 相关代码前必读）

三个插槽**各自独立**解析供应商，留空则按 `rerank_provider → llm_provider → rag_provider` 兜底：

| 插槽 | 配置项 | 本项目实际指向 |
|---|---|---|
| LLM | `LLM_PROVIDER` | 第三方中转站（`RAG_PROVIDER=dashscope` 槽位 → tokenrhythm） |
| Embedding | `EMBEDDING_PROVIDER` | 阿里云百炼官方（`bailian` 槽位 → `qwen3.7-text-embedding`，1024 维） |
| Rerank | `RERANK_PROVIDER` | 留空跟随 LLM；要跟 embedding 那家走必须**显式指定** |

- **供应商名与端点是一一对应的**：`dashscope`（本项目历史指向中转站）/ `bailian`（阿里云百炼官方）
  / `zhipu` / `mock`。注册表在 `app/providers/factory.py::_resolve_endpoint`，加新供应商只需加一个分支
- **百炼的 rerank 不在 OpenAI 兼容层**：`{base_url}/rerank` 实测 **404**，必须走原生端点
  `/api/v1/services/rerank/text-rerank/text-rerank`，且响应在 `output.results`（不是顶层 `results`）。
  故有独立的 `DashScopeRerank` 实现，由 `build_reranker` 对 `bailian` 特判
- **换 embedding 维度必须重建向量集合**（维度在建集合时钉死）：
  `python scripts/reindex_embeddings.py --dry-run` 预览 → 去掉 `--dry-run` 执行。
  它从 SQLite 的 `Chunk.content` 重新编码，**不需要原始文档**
- **测试不得依赖本机 `.env`**：`tests/conftest.py` 已把供应商钉成 mock。没有这层保护时，
  把 `.env` 切到真实供应商会让单测**真实联网**（消耗线上额度），并因向量维度漂移集体失败

## 生成参数与答案质量（K3，改 prompt / 生成相关代码前必读）

- **system 与 user 分离**：规则类约束走 `LLMProvider.complete/stream(..., system=...)`，
  user 段只留「资料 + 问题」。新增 provider 实现必须能吃下 `system`（MockLLM 忽略即可），
  否则整条链路在 mock 模式下直接 TypeError。契约见 `app/rag/generator.py::SYSTEM_PROMPT`
- **`LLM_MAX_TOKENS` 含推理 token**。推理模型（qwen3.8-flash 等）的 `reasoning_tokens`
  与正文**共用**这一份预算：给 1024 时 reasoning 实测吃掉 922，正文只剩 ~100
  → `finish_reason=length`，答案截在句子中间甚至为空（**HTTP 仍是 200**）。
  默认 2048；`OpenAICompatLLM.complete()` 对 `length` 记 WARNING。
  改这个值前先想清楚：结论不是"越大越好"，而是"别让思维链把正文挤没了"
- **`LLM_ENABLE_THINKING` 是三态字符串**（`""` / `true` / `false`），留空 = **不下发**该字段。
  不要改成 `bool | None` —— `.env` 里写 `LLM_ENABLE_THINKING=`（留空）会直接启动报错。
  实测关掉思考整轮 4.1s/问 vs 10.6s/问，且正文更完整
- **文档名必须在生成之前解析**：`RagPipeline._filename_map()` 在 `ask()` / `ask_stream()`
  里都先于 `generator` 调用，生成与来源展示共用同一份映射。
  别把 `_attach_filenames` 那种"生成后再补"的写法加回来
- **来源头含数字**（「[1]（来源：x.pdf · 第 3 块）」），`MockLLM` 里有专门的
  `_SOURCE_HEADER` 把它剥掉，否则会被 `_FACT_RE` 当成事实句混进答案
- **离线答案不能是空串**：`Generator` 在模型返回空白时给 `LLM_EMPTY_ANSWER`
  （与 `NO_HIT_ANSWER` 语义不同：一个是"资料里没有"，一个是"模型没答上"）
- 评测脚本：`python scripts/k3_eval.py --kb 1 --out docs/eval/k3-after.md`
  （13 问固定问题集，机器判定命中/点名/拒答/耗时；对新旧代码都能跑，用于 A/B 归因）

## 日志与错误提示（K8，写日志 / 改错误处理前必读）

- **只配 root，别在模块里自己 `addHandler`**：`app/logging_config.setup_logging()` 在
  `app/main.py` 导入时调用一次，root 持唯一 handler，`app` 记录器 `propagate=True`。
  自己再配一个 named logger 会让同一条日志打印两遍，并且绕开 `LOG_LEVEL`
- **`LOG_LEVEL` 可配**（默认 `INFO`）。**排查"检索搜不到 / 答案被截断 / 重排不准"必须先开 DEBUG**
  —— 零命中、重排退化这类细节在 INFO 下是静默的，这是刻意设计（每次检索都会走到，INFO 会淹掉正常日志）
- **每条日志自动带 `request_id`**：由 `LogRecordFactory` 注入（不是 handler filter，
  否则换个 handler 就丢字段）。中间件 `RequestIdMiddleware` 负责透传/生成 `X-Request-ID`
- **⚠️ 未处理异常处理器跑在中间件之外**：异常先冒泡出中间件、`finally` 重置 contextvar，
  之后最外层 `ServerErrorMiddleware` 才调用 `@app.exception_handler(Exception)`。
  所以处理器里**必须**用 `request.state.request_id` + `request_id_scope()` 重建上下文，
  否则最关键的那条 500 日志反而没有 id。同理，**500 响应头由 `_internal_error_response()`
  自己设**，不能指望中间件的 `send` 包装（`ServerErrorMiddleware` 用自己的 `send`，绕过用户中间件）
- **新增的 `except` 块一律要留痕**：能自愈的用 `warning`（401、改写降级、重排退化），
  需要人介入的用 `exception`（摄取失败、未处理异常）。**只 `raise` 不打日志是缺陷**，
  本仓库已经因此丢过一整条链路（非流式问答，见 bugfix-log #28）
- **失败路径必须有日志，成功路径不用**：日志量按"出问题时够不够定位"来定，不按"覆盖全不全"来定

### 前端错误提示（K8）

- 一律用 `@/api/error` 的 `extractErrorMessage(err, "兜底文案")`。
  **禁止再写 `err.response?.data?.detail || "..."`** —— FastAPI 的 `detail` 在 422 时是**数组**，
  直接塞进 `ElMessage.error` 会渲染成 `[object Object]`（bugfix-log #36）
- `isAbortError(err)` 用来区分"用户主动停止"和"请求失败"，前者不该标红
- 页面加载态用**三态**（加载中 / 加载成功 / 加载失败），不要只用一个 `loaded: boolean`
  —— 失败时 `loaded` 恒为 false 会让页面永久停在「加载…」（bugfix-log #37）
- 不引入全局错误 toast：每个调用点自己弹提示，拦截器再弹一次会重复（决策记录见 bugfix-log 本轮"不做"）

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
- `tests/conftest.py` 把 `RAG_PROVIDER` / `EMBEDDING_PROVIDER` 钉成 `mock`、`RERANK=false`，
  保证测试**不读本机 `.env`、不出网、可重复**；`LLM_PROVIDER` / `RERANK_PROVIDER` 钉成**空串**
  （不设成 mock，否则 `test_rerank.py` 里验证真实装配的用例会静默失去覆盖）
- pgvector 相关用例单独标记，连不上自动跳过
- 统一用 `TestClient(app, raise_server_exceptions=False)`
- 清理测试数据走原生 psycopg2（规避 Python 3.13 上 SQLAlchemy immutabledict 的问题）

## 环境

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"        # 后端 + 测试依赖
pip install -e ".[api]"        # 需要 API 服务时
copy .env.example .env         # 填 DASHSCOPE_API_KEY（LLM）/ BAILIAN_API_KEY（Embedding）
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
| 真实 Embedding K0 / 真流式生成 K1 / 管线单例 K2（阶段五） | ✅ |
| 答案质量 K3 批 1（system 分离 + 来源元信息 + 生成参数配置化） | ✅ |
| 可观测性与错误提示 K8（日志可配 + request-id + 静默点开口 + 前端错误归一化） | ✅ |
| 答案质量 K3 批 2（引用校验 / 拒答分级 / 阈值）/ K4 摄取异步化 | ⬜ |

**273 测试全绿**（160 通过 / 113 跳过，跳过项需 PostgreSQL 或真实 API Key）。未排期项见 `docs/roadmap.md` 末尾（反馈日志、数据看板、异步摄取队列、向量库可插拔、多模型配置、审计日志、SSO/LDAP、更多格式、RPA 集成）。
