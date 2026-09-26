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

### 1. `Settings` 真单例（`app/config.py`）— **本块决定不做**

> ⏸ 见「关于 K2-1」。把构造提到 lifespan 之后，chat 链路已不再每请求读 `.env`，
> 而进程级缓存会破坏测试的 `DATA_DIR` 隔离。以下保留原始设想备查。

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

### 3. 请求侧改为取用

统一入口是 `app/api/deps.py` 里的两个垫片 —— 缺失时给清晰 `RuntimeError`
而不是裸 `AttributeError`（`TestClient` 忘了用 `with` 时最容易踩）：

```python
def get_rag(request) -> RagPipeline: ...
def get_ingestion(request) -> IngestionPipeline: ...
```

- `chat.py`：删掉 `_build_rag`，2 处调用点改 `get_rag(request)`
- `documents.py`：删掉 `_build_ingest`，2 处调用点改 `get_ingestion(request)`
- `inspect.py` / `diagnostics.py` / `kbs.py`：见上面「实际覆盖的调用点」表；
  其中 `_check_consistency(kb_id, docs)` 改成 `(kb_id, docs, store)` 显式传向量库

### 4. 连接池显式化（可选但推荐）

- `pgvector_store.py` 的 `create_engine()` 补 `pool_size=5, max_overflow=10, pool_pre_ping=True`
  （`pool_pre_ping` 防 PG 空闲断连后第一个请求报错）
- `openai_compat.py` 的 `OpenAI(...)` 显式传 `timeout=` 与 `max_retries=2`
  （现在完全用 SDK 默认，**没有超时上限**，真模型挂住时会一直等）

## 子块

| 子块 | 功能 | 说明 | 状态 |
|------|------|------|------|
| K2-1 | `get_settings()` 真单例 + `cache_clear` | 修掉名不副实的注释 | ⏸ **本块跳过**，理由见下 |
| K2-2 | lifespan 装配 `app.state.rag` / `app.state.ingestion` | 启动即建，失败则启动失败（早暴露） | ✅ |
| K2-3 | 请求侧改取用，删 `_build_rag` 重建逻辑 | 实际覆盖 5 个调用点（见下） | ✅ |
| K2-4 | engine 池参数 + OpenAI timeout/retries 显式化 | 顺手补一个真实风险 | ✅ |

### K2-3 实际覆盖的调用点（比原计划多两处）

排查发现"每请求重建"不止 chat：

| 位置 | 原行为 | 现行为 |
|---|---|---|
| `api/chat.py` `_build_rag`（2 处调用） | 每次建 llm + embedding + vector_store + reranker | `get_rag(request)` |
| `api/documents.py` `_build_ingest`（2 处调用） | 每次建 embedding + vector_store | `get_ingestion(request)` |
| `api/inspect.py` | **卡片未提**：每次建 embedding + vector_store + reranker | 复用 `get_rag(request).retriever` |
| `api/diagnostics.py` `_check_consistency` | **卡片未提**：每次建 vector_store | 由调用方传入 `app.state.rag.vector_store` |
| `api/kbs.py` 删库清向量 | **卡片未提**：每次建 vector_store | 复用 `app.state.rag.vector_store` |

## 输入 / 输出

- 对外 API 契约**完全不变**（纯内部重构）
- 新增：`app.state.settings` / `app.state.rag` / `app.state.ingestion`（供测试与路由取用）
- 新增：`RagPipeline.vector_store` / `RagPipeline.embedding` 两个公开属性（供摄取链路共用）

## 验收标准（可测试）

- [x] 同一 app 内两次取用返回**同一对象**（`app.state.ingestion.vector_store is app.state.rag.vector_store`）
- [x] 连续 10 次 `/ask` + 上传 + 质检 + 体检后，`build_llm` / `build_embedding` /
      `build_vector_store` / `IngestionPipeline` 构造计数**全部为 1**（monkeypatch 计数验证）
- [x] `/ask` 与 `/ask/stream` 全链路行为不变：**99 passed / 113 skipped**（零回归）
- [x] 上传文档 → 摄取成功，且 `IngestionPipeline` 只构造一次
- [x] 启动冒烟：真实 `.env`（dashscope + chroma）下 lifespan 正常装配，`/health` 可达
- [x] 实测：**每请求固定开销 652.9ms → 0**（10 次请求省 5876ms，`scripts/k2_assembly_bench.py`）
- [ ] 真 Key 下端到端复测 —— 需外网 + 换新 Key，待办
- [ ] `docker compose up --build` —— 需本机 Docker Desktop 启动，待办
- [n/a] ~~`get_settings() is get_settings()`~~ —— 本块不做，见下

### 关于 K2-1（`get_settings()` 加 `lru_cache`）：本块跳过

复核后**决定不做**：

1. **收益与风险不成比例**。省下的是每次 `.env` 重读（**亚毫秒级**），而 K2 的收益
   （每请求 653ms）100% 来自"不重建 provider"—— 把构造提到 lifespan 后 `_build_rag`
   已消失，chat 链路自然不再每请求读 `.env`。
2. **它会破坏测试隔离**。`tests/test_streaming.py::api` fixture 靠
   `monkeypatch.setenv("DATA_DIR", tmp)` 隔离数据目录；`get_settings()` 一旦变成进程级
   单例，前面测试（如 `test_auth`）已把默认配置缓存住，这个 `setenv` 就失效 ——
   该用例会往**真实 `data/` 目录**写数据。
3. 更干净的等价做法是 `app.state.settings` 传导（不引入全局状态），但要多改 4–5 个文件
   的调用点，留作后续按需再做。

## 实测记录（2026-09-26）

`scripts/k2_assembly_bench.py`，本机，`provider=dashscope` / `vector_store=chroma`：

| 装配点 | 中位耗时 |
|---|---|
| `build_llm` | **632.7 ms** |
| `build_embedding` | 0.0 ms（`.env` 里 `EMBEDDING_PROVIDER=mock`；真实 embedding 同量级） |
| `build_reranker` | 0.0 ms（`rerank=False` → NoopRerank） |
| `build_vector_store` | 20.2 ms |
| **单次请求固定开销** | **652.9 ms** |

改造前 10 次请求 = 6529ms；改造后 = 653ms（仅启动付一次），**省 5876ms**。

**根因（cProfile 定位，非推测）**：`ssl.SSLContext.load_verify_locations` 单次 ~217ms；
httpx 在 `trust_env=True`（默认）下会为探测到的系统代理各建一个传输，因此**一个 client
要建 3 个 SSLContext**（1 主 + 2 代理）→ ~650ms。

| 构造方式 | 耗时 |
|---|---|
| `httpx.Client(timeout=60)`（默认） | ~657 ms |
| `httpx.Client(timeout=60, trust_env=False)` | ~215 ms |
| `httpx.Client(timeout=60, verify=<预建 SSLContext>)` | ~0.2 ms |

**副产物发现**：`trust_env=True` 在 Windows 上会读**注册表里的系统代理**
（本机读到 `127.0.0.1:5810`）。即：开着系统代理时，应用的模型调用会走代理，
同时构造成本从 1× 变 3×。对需要出海访问的用户这是想要的行为；若想让启动再快
~650ms，可给 provider 传一个共享的预建 httpx client —— 属独立优化，不在本块范围。

**两条测法备注**：

1. `create_engine()` 本身只要 0.13ms。卡片里"重建 engine 很贵"指的是**换掉 engine 会让
   连接池失效**（下次请求要重做到 PG 的 TCP 连接），不是这个调用本身贵 —— 两件事要分开说。
2. `OpenAI` SDK 对 `timeout` 传 float 时存 float、传 None 时存 `httpx.Timeout`，
   断言超时要兼容两种形态（`getattr(t, "read", t)`）。


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

## 失败点 / 风险（含实际处置）

- ~~**测试隔离被破坏**（最大风险）~~ → **已规避**：K2-1 不做（不引入进程级缓存），
  改为 lifespan 单实例 + 请求级取用，`monkeypatch.setenv("DATA_DIR", ...)` 的隔离机制
  完全不受影响。
- **lifespan 里构造失败 → 应用起不来** → 符合预期（早暴露）。已用真实 `.env`
  （dashscope + chroma）冒烟验证可正常启动；`PgVectorStore.engine` 保持惰性，构造不连库。
- **HTTP 长连接被服务端掐断** → **已处理**：PG engine 补 `pool_size=5` /
  `max_overflow=10` / `pool_pre_ping=True`；OpenAI client 补 `timeout=60s` / `max_retries=2`。
- **`app.state` 在测试里未设置就访问** → **已处理**：`deps.get_rag` / `get_ingestion`
  缺失时抛带指引的 `RuntimeError`（提示"TestClient 需用 `with` 上下文触发 lifespan"），
  有专门用例覆盖。
- **新增风险：`trust_env=True` 会走系统代理**（Windows 下读注册表）。若部署环境不希望
  走代理，需显式传 `http_client` 并设 `trust_env=False`。当前保持默认 —— 对本机需要
  出海访问的场景正是想要的行为。
