# 需求卡片：K4-摄取异步化与进度反馈

> **本积木解决的核心痛点**：上传文档是**全程同步阻塞**的。`app/ingestion/pipeline.py:82`
> 的 `self.embedding.embed(chunks)` 一次把全部切片发给 embedding 接口，
> `openai_compat.py:44-48` 按 16 条一批循环。一份 100 页 PDF ≈ 400–600 个切片 ≈
> 25–40 个 HTTP 往返，配真 Key 时实测可达 **数十秒到数分钟**。
> 期间浏览器一直转圈，且极易撞上网关/浏览器超时 —— 用户以为"上传失败"，
> 实际后台还在跑。这是"接 Key 会变慢"里体感最差的一环。

## 一句话

上传立即返回 `202` + `status=pending`，解析/切片/向量化丢到后台任务跑，
前端以进度条展示"已处理 X/Y 块"，失败可一键重试。

## 背景与动机

现状链路（`app/api/documents.py` → `IngestionPipeline.ingest_file`）：

```
HTTP 请求 ──► parse_file()            (阻塞)
          ──► _chunk()                 (阻塞，structure+semantic 时还要 embed 每句)
          ──► embedding.embed(chunks)  (★ 阻塞最久：25~40 次 HTTP 往返)
          ──► 写元数据 + 写向量库
          ──► 返回 200
```

问题：

1. **请求全程挂起**，没有任何进度信息，用户无法区分"在跑"和"卡死"。
2. **无法重试**。失败只落一条 `status=failed` + `error`，没有重新摄取的入口，
   用户只能删掉重传。
3. **并发上传会互相拖慢**。多个同步请求各自跑 embedding 批次，没有并发上限。
4. **`structure+semantic` 策略下更糟**。`_chunk` 会逐句调 `embedding.embed`
   （`chunker.py:152-178` 的 `split_semantic`），HTTP 往返次数再翻倍。

## 方案

### 1. 数据模型：新增 `IngestionJob` 表（`app/core/models.py`）

**关键约束**：`Document` 表已存在，`create_all()` **不会给已存在的表加列**，
而 SQLite 分支没有迁移路径（项目已踩过：`knowledge_bases has no column named user_id`）。
→ **不往 `Document` 加 progress 字段**，改为**新建一张表**（新表由 `create_all` 直接建，
零迁移痛苦，这也是 J2 采用过的做法）。

```
IngestionJob:
  id
  document_id    FK documents.id, CASCADE
  kb_id          FK knowledge_bases.id, CASCADE
  user_id        FK users.id
  stage          "parsing" | "chunking" | "embedding" | "indexing" | "done" | "failed"
  done_units     int   # 已处理切片数
  total_units    int   # 总切片数（chunking 完成后回填）
  error          Text nullable
  started_at / finished_at
```

### 2. 摄取拆分（`app/ingestion/pipeline.py`）

- 新增 `ingest_file_async(kb_id, path, display_name) -> Document`：
  只做"建 Document(status=pending) + 建 IngestionJob + 提交后台任务"，立即返回。
- 新增 `_run_job(job_id, path)`：真正的重活，分阶段更新 `stage` 与 `done_units`：
  - parsing → `parse_file`
  - chunking → `_chunk`，完成后写 `total_units`
  - embedding → **按批调用并逐批累加 `done_units`**（把 `embed()` 的循环拆到这一层，
    或给 provider 加 `embed_iter(texts) -> Iterator[list[float]]`）；
    注意 `structure+semantic` 策略下 `_chunk` 内部也在调 embedding，
    这部分计入 `chunking` 阶段（不追求精确，追求"有反馈"）
  - indexing → 写向量库
  - done / failed（写 `error`，同时置 `Document.status`）
- **保留 `ingest_file` 同步版本**（demo 脚本、既有测试在用，同步路径不能断）。

### 3. 执行器与并发上限

- 用 `concurrent.futures.ThreadPoolExecutor(max_workers=2)` 或 FastAPI `BackgroundTasks`。
  选 `ThreadPoolExecutor`：能做并发上限、能查任务状态；`BackgroundTasks` 无上限控制。
- 执行器放 `app.state`（与 K2 一起装配），随应用生命周期创建/关闭。
- 启动时自愈：lifespan 里把所有卡在 `processing` 的 job 置为 `failed`
  （原因写"服务重启中断"），避免永久转圈。

### 4. 接口（`app/api/documents.py`）

- `POST /api/kbs/{kb_id}/documents` → 改为 **202**，body 返回
  `{id, filename, status: "pending", job: {stage, done_units, total_units}}`
- `GET /api/documents/{id}` → 响应增加 `job` 对象（stage / done / total / error）
- 新增 `POST /api/documents/{id}/retry` → 对 `failed` 的文档重新入队；非 failed 返回 409
- 权限：沿用现有 `_get_user_kb_or_403` + 角色门控（editor+ 才能上传/重试）

### 5. 前端（`DocList.vue`）

- 上传后立即把行插入列表并显示"处理中"，**每 2 秒轮询** `GET /api/documents/{id}`
  （只在列表里存在非终态文档时轮询；全部终态后停止定时器——别做成永久轮询）
- 进度条用 `done_units / total_units`；`total_units` 为 0 时显示不定态（"解析中…"）
- `failed` 行显示原因 + "重试"按钮
- 页面离开或组件卸载时清理定时器

## 输入 / 输出

- `POST /api/kbs/{kb_id}/documents` → **202**（原 200）+ `job` 字段
- `GET /api/documents/{id}` → 增加 `job: {stage, done_units, total_units, error}` | null
- `POST /api/documents/{id}/retry` → 202 + job；非 failed → 409；无权限 → 403

## 验收标准（可测试）

- [ ] 上传接口在 mock 模式下立即返回（用超时断言：响应 < 300ms），且 `status == "pending"`
- [ ] 轮询 `GET /api/documents/{id}`：能看到 `stage` 从 parsing 递进到 done，`status` 最终 `indexed`
- [ ] `total_units` 在 chunking 完成后 == 实际 `Document.chunk_count`
- [ ] 失败路径：传一个损坏/空文件 → `status=failed`、`error` 非空、job `stage=failed`
- [ ] `POST /retry`：failed → 202 且重新跑成功；对 indexed 文档 → 409
- [ ] 权限：viewer 调 upload/retry → 403；他人 KB → 403
- [ ] 并发：同时上传 3 个文件，全部成功（执行器上限内排队，不互相覆盖状态）
- [ ] 重启自愈：手动把某 job 置 `processing` 后重启应用 → 该 job 变 `failed`
- [ ] 同步 `ingest_file()` 行为与返回结构完全不变（回归）
- [ ] 现有 187 测试全绿（注意：依赖"上传后立即 indexed"的用例需改为轮询等待）
- [ ] `npm run build` 通过

## 依赖

- 前置：K2（执行器与单例一起装配在 `app.state`，两者天然合做）
- 复用：`_get_user_kb_or_403`、现有角色门控、`Document.status` 状态机
- 新增依赖：无（`concurrent.futures` 是标准库）

## 不做什么（边界，防止范围蔓延）

- **不引入 Celery / Redis / RQ**。作品要能"一条 `docker compose up` 起全栈"，
  加消息队列会带来一个额外基础设施依赖，收益不值。进程内线程池足够。
- 不做 SSE/WebSocket 实时进度推送（轮询足够，且实现简单不易出错）
- 不做断点续传（失败就是重跑整个文档）
- 不做批量上传的队列排序/优先级
- 不做嵌入结果的磁盘缓存（属 K6）

## 失败点 / 风险

- **测试大面积受影响**（最大工作量所在）。现有用例大概是"上传 → 断言 indexed"，
  改成异步后必须变成"上传 → 轮询至 indexed"。→ 建议在测试里封装
  `wait_indexed(client, doc_id, timeout=10)` 辅助函数，逐处替换。
- **`BackgroundTasks` / 线程池里的数据库会话**：后台线程不能复用请求的
  `Depends(get_db)` 会话。→ 后台任务内部自己 `with get_db(session_factory) as db`，
  与 `IngestionPipeline` 现有写法一致。
- **`done_units` 频繁写库**：按批更新（每批一次），不要每个切片都写。
- **进度不准**：`structure+semantic` 策略下 chunking 阶段耗时会远大于 embedding 阶段，
  进度条会显得"卡在 50% 很久"。→ 在 UI 文案上允许并说明（"语义切分中…"）。
- **SQLite 并发写**：SQLite 单写锁，多个 job 同时写元数据可能 `database is locked`。
  → 元数据写入集中在少数几个阶段点（不逐批），或给 engine 设 `timeout`。
  这是 SQLite 分支的固有限制，记录下来即可；pgvector 分支无此问题。
- **`Document` 表若未来需要加 progress 字段** → 必须同时给 SQLite 写一个
  "缺列则 ALTER" 的轻量迁移函数，否则会重现 `no column named` 事故。本积木刻意绕开，
  但要在 `AGENTS.md` 的数据库章节记一笔。
