# 需求卡片：K1-真流式生成（首字节延迟优化）

> **本积木解决的核心痛点**：现在 `POST /ask/stream` **不是流式**。它在 `app/api/chat.py:322`
> 先同步调用 `rag.ask()` 等完整答案生成完（真模型 3–10 秒），再用 `:335-341` 把整段
> 字符串按 3 字符切片"假装打字机"。配 mock 时肉眼无感（生成瞬时），**一旦接入真实 API Key，
> 用户会先盯着空白等 3–10 秒，然后看到一段假动画**——这是"接 Key 变慢"体感的最大来源。

## 一句话

让答案**真的逐 token 从大模型流出来**：给 `LLMProvider` 加 `stream()`，`RagPipeline` 加
`ask_stream()`，`/ask/stream` 边检索边推事件（阶段 → 来源 → token），前端先显示"正在检索…"
再逐字出答案。首字节延迟从 3–10 秒降到 0.5–1.5 秒。

## 背景与动机

现状链路（`app/api/chat.py:304-366`）：

```
请求 → _build_rag() → rag.ask()  ← ★ 在这里阻塞等完整答案 3~10 秒，SSE 一个字节都没发
                   → 建 QaLog / 落库
                   → event_stream() 才开始 yield，切 3 字符一片
```

问题不只是"慢"，还有三个连带伤害：

1. **无法评估答案质量**。想改 prompt（K3）时，每次验证都要盲等 8 秒才看到全文，
   迭代成本极高。
2. **长回答必然触发超时**。nginx `proxy_read_timeout 300s`、浏览器/网关默认 60s，
   一段 2000 字回答在 qwen-plus 上要 15–30 秒，全程零字节输出，容易被判定为空闲连接。
3. **失去"流式"这个卖点**。作品README写着"SSE 流式问答"，面试官点开一看是先卡后打字，
   属于可以被当场拆穿的虚标。

## 方案

### 1. Provider 层：加流式接口（`app/providers/`）

- `base.py`：`LLMProvider` 新增
  ```python
  def stream(self, prompt: str, *, max_tokens: int = 1024) -> Iterator[str]:
      """逐段产出增量文本。默认实现 = 调 complete() 后一次性 yield（向后兼容）。"""
      yield self.complete(prompt, max_tokens=max_tokens)
  ```
  默认实现放在基类，**三个现有实现（Mock/OpenAICompat/未来新增）不改也不会挂**。
- `openai_compat.py`：覆写 `stream()`，用 `client.chat.completions.create(stream=True)`，
  遍历 `chunk.choices[0].delta.content`，过滤 None 后 yield。
- `mock.py`：不覆写（继承基类默认）→ 离线行为、测试断言全部不变。

### 2. 编排层：`RagPipeline.ask_stream()`（`app/rag/pipeline.py`）

新增生成器方法，**事件化输出**，与 `ask()` 并存（`ask()` 保持不动，非流式接口零回归）：

```python
def ask_stream(self, kb_id, query, top_k=None, mode=None, history=None) -> Iterator[dict]:
    # 1. 改写 → yield {"type": "stage", "stage": "rewriting"}
    # 2. 检索 → yield {"type": "stage", "stage": "retrieving"}
    # 3. 重排 → yield {"type": "stage", "stage": "reranking"}
    # 4. ★ 先把来源推出去（此时已知，用户能立刻看到引用卡片）
    #    yield {"type": "sources", "sources": [...], "rewritten_query": ..., "stage": "generating"}
    # 5. 逐 token 流式生成 → yield {"type": "token", "content": tok}
    # 6. yield {"type": "done", "answer": 完整答案}   ← 供调用方落库
```

关键点：**来源在生成之前就能确定**（检索+重排已完成），所以可以先推来源、再推 token。
这比现在"答案吐完才给来源"体验好得多。

### 3. 接口层：`/ask/stream` 改为真流式（`app/api/chat.py`）

- **去掉生成前的 `try/except → 502`**。流已经开始后没法再改 HTTP 状态码，
  错误必须变成流内事件：`yield {"type": "error", "message": "..."}`。
- **`_log_qa` / `_save_turn` / `_touch_conversation` 必须移进生成器内部**，
  在收到 `{"type": "done"}` 之后执行。现在是流开始前执行（因为答案已完整），
  改真流式后**答案还不存在**，直接搬会落一条空消息。
- **心跳**：每次 `stage` 事件前发一行 SSE 注释 `: ping\n\n`，维持连接活性。
- `X-Accel-Buffering: no` 已在（`:364`），保留。

### 4. 前端：Chat.vue

- 发送后立即插入一条助手占位消息，按 stage 显示状态文案：
  `正在改写问题…` → `正在检索资料…` → `正在重排…` → `正在组织答案…`
- 收到首个 `token` 后清空状态文案，开始逐字追加。
- `sources` 事件到达即渲染引用卡片（不必等答案结束）。
- 收到 `error` 事件 → 该条消息转为错误态，可重试。

## 子块

| 子块 | 功能 | 说明 |
|------|------|------|
| K1-1 | `LLMProvider.stream()` 基类默认 + OpenAICompat 覆写 | Mock 不覆写，离线零回归 |
| K1-2 | `RagPipeline.ask_stream()` 事件化生成器 | `ask()` 不动 |
| K1-3 | `/ask/stream` 真流式 + 落库后置 + 错误事件化 | 关键：日志/落库移入生成器 |
| K1-4 | Chat.vue 阶段文案 + 早出引用 | — |

## 输入 / 输出

- SSE 事件序列（新增 `type` 取值，前端需兼容旧 `token`/`sources`/`[DONE]`）：
  - `{"type":"stage","stage":"rewriting|retrieving|reranking|generating"}`
  - `{"type":"sources","sources":[...],"rewritten_query":"...","conversation_id":N}`
  - `{"type":"token","content":"..."}`
  - `{"type":"error","message":"..."}` ← 新增
  - `{"type":"done","answer":"完整答案","conversation_id":N}` ← 新增
  - `data: [DONE]` 保留（兼容旧前端）

## 验收标准（可测试）

- [x] `MockLLM.stream()` 输出拼接结果 == `MockLLM.complete()` 输出（回归等价）
- [x] `OpenAICompatLLM.stream()` 用 stub client 断言：按 delta.content 顺序产出、过滤 None
- [x] `ask_stream()` 事件顺序：`stage(rewriting) → stage(retrieving) → stage(reranking) → sources → token+ → done`
- [x] `sources` 事件在**第一个 token 之前**到达（这是体验改进的核心断言）
- [x] `ask()`（非流式）行为、返回字段、耗时特征与改动前完全一致
- [x] 生成中途抛异常 → 流内出现 `error` 事件，HTTP 状态码仍为 200，连接正常关闭
- [x] 带 `conversation_id` 时：`done` 之后才落库，且 assistant 消息 content 完整（非空、非半条）
- [x] 检索为空 → 直接推 `sources: []` + 固定文案，不调 LLM
- [ ] 前端：mock 模式下打字机仍流畅；真 Key 模式下首屏 1.5 秒内出现"正在检索资料…"
      —— 前半段已验（事件序列离线断言 + `npm run build` 通过），**后半段待换新 Key 后真机实测**
- [x] 现有 187 测试全绿 → 实跑 **92 passed / 113 skipped**（skipped 全部是 PostgreSQL 未启动的既有条件跳过，非回归）
- [x] `npm run build` 通过（`vue-tsc --noEmit` 通过 + `vite build` 通过）

## 实测记录（2026-09-26）

`tests/test_streaming.py` 新增 18 个用例，**全部离线可跑**（mock 供应商 + 临时目录，不依赖
PostgreSQL / 真实 Key）。接口层的用例通过 monkeypatch 把 `DATA_DIR` 指向 tmp_path，
因此既不碰真实 `data/` 目录，也不受 PG 是否启动影响 —— K1 的验收点都有自动化覆盖。

首字节延迟实测（`scripts/k1_stream_latency.py`，桩模型模拟首字延迟 1.5s、之后每 0.2s 一个 token）：

| 时间点 | 改造前 | 改造后 |
|---|---|---|
| 首字节 | 1.54 s（= 等整段生成完） | **0.05 s**（第一个 stage 事件） |
| sources 可见 | 整段结束后 | **0.06 s** |
| 首个 token | — | 1.76 s（含模型首字延迟） |
| 整段结束 | 1.54 s | 2.61 s |

结论：**首字节不再等于整段生成耗时**，与模型首字延迟解耦。模型首字延迟越大差值越大
（昨日实测 qwen3.8-flash 单问 16.7s，其中绝大部分原本是零反馈的等待）。

> 测法备注（下次别重踩）：
> ①`TestClient` / `httpx.ASGITransport` 都会把响应体整体缓冲，测出的"首字节"其实是"整段结束"，
> 必须在 ASGI 层记录 `http.response.body` 帧的发送时刻；
> ②Starlette 的 `StreamingResponse` 会并发跑 `listen_for_disconnect`，**谁先结束谁取消对方** ——
> 手工驱动 http.client 时 `receive()` 第二次必须挂起，返回 `http.disconnect` 会把刚启动的流一起取消。

## 依赖

- 前置：B6-2（SSE 通道）、J2（会话落库，因为要动落库时机）
- 复用：现有 `Generator`/`Reranker`/`Retriever`，不改检索链路
- 新增依赖：无

## 不做什么（边界，防止范围蔓延）

- **不做停止生成/中断**（用户点"停止"→ 后端 abort）。同步生成器里检测断连要改成 async，
  与本积木耦合太深，单独排 K8。
- 不做 token 用量统计与成本显示（真接 Key 后再谈）
- 不改非流式 `/ask` 接口的响应结构
- 不做 WebSocket 双向通道（SSE 够用）
- 不改 prompt 内容（明确留给 K3，别在一个积木里混两件事）

## 失败点 / 风险

- **落库时机搬错位置** → 最危险的一条。必须写完"`done` 之后再落库"的测试，
  否则会出现"消息列表里答案空白"的静默 bug。
- **`db` 会话跨生成器生命周期** → `Depends(get_db)` 的 Session 在 StreamingResponse
  结束前不会关闭，但生成器在线程池里跑，需确认 Session 未被提前 close。
  验收：带 `conversation_id` 的流式问答落库成功后，`GET /conversations/{id}` 能查到完整消息。
- **心跳缺失导致网关掐连接** → 每个 stage 前发 `: ping`，且生成首 token 前至少发一次。
- **前端旧事件处理不兼容** → `type` 字段是新增的，前端需先判 `type` 再兜底旧格式。
- **`_build_rag` 每请求重建仍在**（K2 处理）→ 本积木会把首字节里"重建管线"那段固定开销
  暴露得更明显，属于已知且已排期，不在本积木修。
