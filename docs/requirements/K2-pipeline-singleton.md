# 需求卡片：K2-管线单例与连接复用（每次请求省掉重建开销）

> **本积木解决的核心痛点**：`app/api/chat.py:126-139` 的 `_build_rag()` **每个请求都重建
> 整个 RagPipeline**。而 `build_vector_store()` 会 new 一个 `PgVectorStore`（`create_engine`
> → 新建连接池）、`build_llm()` 会 new 一个 `OpenAI(...)`（新建 httpx 池 → **对 DashScope
> 重做 TLS 握手 + 连接建立**）。每问一句话白送 300–800ms，且随并发线性恶化。

## 一句话

把 `RagPipeline` 及其所有 provider/向量库实例在应用启动时**建一次**，挂到
`app.state`，请求直接复用；顺带让 PG engine 与 OpenAI client 全局共享连接池。

## 背景与动机

现状：`_build_rag(request)` 在**每次** `/ask`、`/ask/stream` 调用时执行：

```python
settings = get_settings()          # 每次 new Settings（重读 .env 文件！）
return RagPipeline(
    vector_store=build_vector_store(settings),   # → PgVectorStore() → create_engine() 惰性
    embedding=build_embedding(settings),         # → OpenAI(...) 新 httpx 池
    llm=build_llm(settings),                     # → OpenAI(...) 又一个新 httpx 池
    reranker=build_reranker(settings),
)
```

三处可量化的浪费：

1. **`OpenAI(...)` × 2 每请求**：httpx client 不复用 → 每次都要 DNS + TCP + TLS
   （`https://dashscope.aliyuncs.com` 单次握手实测 150–400ms，境内网络波动时更久）。
2. **`create_engine()` 每请求**：SQLAlchemy engine 自带连接池，重建 = 池失效 +
   重新建立到 PostgreSQL 的 TCP 连接。
3. **`Settings()` 每请求**：pydantic-settings 每次都会去读 `.env` 文件（磁盘 I/O），
   尽管函数名叫 `get_settings()`，注释写着"惰性单例"——**实际上并没有缓存**。

同时 `app/ingestion/pipeline.py` 也是同样模式（上传文档时再重建一遍）。

## 方案

### 1. `Settings` 真单例（`app/config.py`）

`get_settings()` 加 `functools.lru_cache`。注意：**测试里有改环境变量后重新读配置的场景**，
需暴露 `get_settings.cache_clear()`；已有测试如因此失败，说明该测试本就依赖"每次重读"的
错误行为，应改为显式传 `Settings(...)` 实例。

### 2. 启动时装配（`app/main.py` lifespan）

在现有 lifespan 里构建一次，挂到 `app.state`：

```python
app.state.session_factory = ...
app.state.rag = RagPipeline(settings=settings, session_factory=session_factory, ...)
app.state.ingestion = IngestionPipeline(settings=settings, session_factory=session_factory)
```

### 3. 请求侧改为取用（`app/api/chat.py` / `documents.py`）

- `_build_rag(request)` → `return request.app.state.rag`（函数保留作兼容垫片，
  或直接替换调用点并删掉它）
- `documents.py` 里摄取相关调用同样改取 `app.state.ingestion`

### 4. 连接池显式化（可选但推荐）

- `pgvector_store.py` 的 `create_engine()` 补 `pool_size=5, max_overflow=10, pool_pre_ping=True`
  （`pool_pre_ping` 防 PG 空闲断连后第一个请求报错）
- `openai_compat.py` 的 `OpenAI(...)` 显式传 `timeout=` 与 `max_retries=2`
  （现在完全用 SDK 默认，**没有超时上限**，真模型挂住时会一直等）

## 子块

| 子块 | 功能 | 说明 |
|------|------|------|
| K2-1 | `get_settings()` 真单例 + `cache_clear` | 修掉名不副实的注释 |
| K2-2 | lifespan 装配 `app.state.rag` / `app.state.ingestion` | 启动即建，失败则启动失败（早暴露） |
| K2-3 | 请求侧改取用，删 `_build_rag` 重建逻辑 | — |
| K2-4 | engine 池参数 + OpenAI timeout/retries 显式化 | 顺手补一个真实风险 |

## 输入 / 输出

- 对外 API 契约**完全不变**（纯内部重构）
- 新增：`app.state.rag`、`app.state.ingestion`（供测试与其他路由取用）

## 验收标准（可测试）

- [ ] `get_settings() is get_settings()` 为 True；`cache_clear()` 后重新读取生效
- [ ] 同一 app 内两次 `_build_rag`/取用返回**同一对象**（`is` 断言）
- [ ] 连续 10 次 `/ask` 后，`build_llm` 调用计数为 1（用 monkeypatch 计数验证）
- [ ] `/ask` 与 `/ask/stream` 全链路行为不变：187 测试全绿
- [ ] 上传文档 → 摄取成功，且 `IngestionPipeline` 只构造一次
- [ ] 实测（真 Key）：同一问题连问 3 次，第 2/3 次的"检索前固定开销"较改前下降
      ≥ 200ms（用日志时间戳或简单埋点对比）
- [ ] `docker compose up --build` 后服务正常起（lifespan 里新增构造不能拖垮启动）

## 依赖

- 前置：无（可独立上，**和 K1 顺序无关，两者改的文件几乎不重叠**）
- 与 K1 交互：K1 让首字节延迟变可见，K2 直接砍掉其中一段固定开销，建议 K1 之后紧接着做
- 新增依赖：无

## 不做什么（边界，防止范围蔓延）

- 不做多进程/多 worker 场景下的共享（`app.state` 是 per-process，uvicorn 多 worker
  各建各的，属预期行为，不引入外部连接池服务）
- 不做向量库/模型的运行时热切换（换配置需重启，接受）
- 不做请求级缓存（同问题重复命中，属 K6）
- 不改任何接口的请求/响应结构

## 失败点 / 风险

- **测试隔离被破坏**（最大风险）：`TestClient(app)` 每次建新 app → `app.state` 新建，
  OK；但 `get_settings()` 变成进程级单例后，**跨测试污染环境变量**。必须先跑一遍
  全量测试，凡是"改了 env 期望配置生效"的用例都要改成显式传 `Settings(...)`。
- **lifespan 里构造失败 → 应用起不来**：这正是想要的（早暴露），但要确认
  `RAG_PROVIDER=mock` + `VECTOR_STORE=chroma` 的默认路径在无 Key 无 PG 时仍能启动
  （当前 `PgVectorStore.engine` 是惰性的，别在构造时强行连库）。
- **HTTP 长连接被服务端掐断**：复用连接后可能出现"空闲过久后用到一个死连接"，
  `pool_pre_ping=True` 与 SDK 自身重试可覆盖大部分；必要时补 `keepalive_expiry`。
- **`app.state` 在测试里未设置就访问** → 加显式断言或小工具函数
  `get_rag(request)`，缺失时给出清晰错误而非 `AttributeError`。
