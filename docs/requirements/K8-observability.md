# 需求卡片：K8-可观测性与错误提示（日志覆盖 + 错误可定位）

> **本积木解决的核心痛点**：系统能跑，但**跑坏的时候查不到**。
> 一次 `/ask` 失败，日志里可能一个字都没有；用户界面弹一句 `[object Object]`；
> 摄取失败只写 DB 不打日志；检索零命中完全静默。
> 排查只能靠"重启试试""换个问题再问一遍"。

## 一句话

把日志从"几个人手动插的调试点"补成一套**能定位问题的观测面**：
级别可配、第三方 logger 统一收编、请求可追踪（request-id）、
已知静默点全部开口说话、前端不再吞错误。

## 背景：审计结论（2026-09-26）

对后端日志基建 + 前端错误提示做了一次只读审计，结论：

| 维度 | 现状 |
|---|---|
| 日志基建 | 只有 `app/main.py:33-39` 一处：`logger("app")` + stderr handler + `setLevel(INFO)` 硬编码 + `propagate=False`；无 root 配置 |
| 覆盖率 | **6/34 模块有 logger、11 处调用；21 个有代码文件零日志**（`rag/` 全目录、`ingestion/` 全目录、`storage/`、`api/deps.py`、`api/auth.py`） |
| 级别 | 只用了 INFO×1 / WARNING×1 / ERROR×9；**DEBUG 静默丢失**（无开启手段）、第三方 logger 完全不受治理 |
| 错误可定位性 | 少数报错写得极好（`openai_compat._assert_dim`），多数只有一句「服务暂时不可用」 |
| 静默失败 | 后端确认 12 处 + 前端确认 8 处 |

三条最严重的：

1. **`chat.py:337-338` 非流式 `/ask` 失败一行日志都没有**，而流式 `chat.py:454-456` 有
   `logger.exception` —— 同一条 RAG 链路两种待遇。
2. **`ingestion/pipeline.py:115-120` 置 `indexed` 那段没有 try/finally**，
   此处一旦异常，文档永久卡在 `processing`，且日志无痕。
3. **`frontend/src/api/chat.ts:151-154` 的 422 数组 detail** 会渲染成 `[object Object]`，
   用户粘贴超 2000 字（`AskRequest.query` 的 `max_length=2000`）即可复现。

## 方案

### 后端

| 编号 | 内容 | 类型 |
|---|---|---|
| K8-1 | `LOG_LEVEL` 配置项（`app/config.py`）+ `app/logging_config.py`：配置 **root** 而非只配 `app`，`app` 改为向上传播；第三方 logger（uvicorn/httpx/chromadb/sqlalchemy）统一收编 | 改进 |
| K8-2 | `X-Request-ID` 中间件：透传或生成、注入日志上下文、回写响应头；500 响应体带 `request_id` | 改进 |
| K8-3 | `chat.py:337` 非流式失败补 `logger.exception`（与流式对齐） | 🔴 bug |
| K8-4 | 鉴权 401 补日志：`deps.py` 无效/过期 token（warning + 原因）、`auth.py` 登录失败（security 事件） | 🟠 bug |
| K8-5 | 静默点开口：向量零命中、`query_rewriter` 改写失败、rerank 退化填 0、`ingestion._fail()` | 🔴 bug |
| K8-6 | 流式截断补告警：`OpenAICompatLLM.stream()` 与 `complete()` 对齐检查 `finish_reason` | 🔴 bug |
| K8-7 | 摄取 step4 加 `try/except`，异常时置 `failed` 而非留 `processing` | 🔴 bug |

### 前端

| 编号 | 内容 | 类型 |
|---|---|---|
| K8-8 | 422 数组 `detail` 归一化，不再渲染 `[object Object]` | 🔴 bug |
| K8-9 | 新增 `api/error.ts` 统一错误归一化，20 处调用点收敛到 `extractErrorMessage(err, 兜底文案)`。**不做全局 toast** —— 各调用点已各自弹提示，拦截器再弹一次会重复 | 改进 |
| K8-10 | `DocHealth.vue` / `Diagnostics.vue` 失败后不再停在「加载…」占位 | 🟠 bug |
| K8-11 | `DocList.vue` 角色获取失败不再静默降级为 viewer | 🟠 bug |
| K8-12 | `Chat.vue` 区分 `AbortError`（用户主动停止 ≠ 请求失败） | 🟠 bug |
| K8-13 | `main.ts` 加 `app.config.errorHandler`，渲染期异常不再白屏无提示 | 改进 |

## 验收标准

> **状态（2026-09-26）：全部通过。**
> 新增回归用例 22 个（`tests/test_observability.py`），后端 251 → **273 collected / 0 failed / 113 skipped**；
> `npx vue-tsc --noEmit` + `npx vite build` 通过。
> 逐条缺陷的现象/位置/根因/修复记录见 [`../bugfix-log.md`](../bugfix-log.md) 第 28–39 条。

1. `LOG_LEVEL=DEBUG` 时 `app.*` 的 debug 消息可见；`LOG_LEVEL=WARNING` 时 info 不可见 —— 不重编译代码即可切换。
2. 任意一个 `/api/...` 响应都带 `X-Request-ID`；把客户端拿到的 ID 贴进日志搜索，能唯一定位到该请求。
3. 未处理异常返回 500 时，响应体含 `request_id` 且与响应头一致。
4. 构造 RAG 抛错，**非流式**与**流式**两条路径都在日志里留下完整堆栈。
5. 向量检索全部低于阈值（零命中）时，日志有对应记录（含 kb_id 与被丢弃条数）。
6. 上传一个解析必失败的文档：日志有 ERROR 带文件名与异常；`Document.status == "failed"`。
7. 摄取 step4 强制抛错时，文档状态为 `failed` 而**不是** `processing`。
8. 用 > 2000 字的问题调 `/ask`：前端显示的是一句可读的中文错误，不是 `[object Object]`。
9. `DocHealth` / `Diagnostics` 请求失败时，页面显示错误态而非永久「加载…」。
10. 全量测试**零回归**（基线 251 collected / 0 failed）。
11. `cd frontend && npx vue-tsc --noEmit && npx vite build` 通过。

## 非范围（明确不做，避免scope creep）

- **`QaLog` 表加 `status` / `duration_ms` / `error` 列**：是数据模型变更，需要迁移方案
  （SQLite `create_all` 不会给已存在的表加列）。**问答失败率统计**是独立议题，另开卡片。
  本次只在日志侧留痕。
- **日志落盘 / 轮转 / JSON 格式 / 接入日志平台**：部署形态未定（Docker + nginx 同源反代），
  先统一 stderr，容器里交给 docker 日志驱动收集即可。
- **CORS 中间件**：`AGENTS.md`「容器化」一节已明确**设计上不需要**（前端同源反代）。
  审计时把它列为"缺失"是我的误判 —— 不做。若将来前后端分域部署再单独评估。
- **前端测试框架（vitest）**：本仓库前端无测试运行时。新增测试框架是独立工程量，
  本批沿用仓库既有约定（`vue-tsc` 类型检查 + `vite build` + 关键路径手工验证）。
  **这也是本批前端改动无法走 TDD 的原因，已记录为遗留风险。**

## 设计原则

- **不改变正常路径行为**：所有改动都是"失败时多说一句话"，成功路径的返回体/事件序列不变。
- **日志不阻塞主流程**：日志写入自身失败不能影响业务（沿用 `chat.py:_log_qa` 的姿势）。
- **级别语义**：`debug`=排查细节（零命中、退化），`warning`=能自愈但值得知道（401、重排退化、
  改写失败），`error`=需要人介入（摄取失败、未处理异常）。
