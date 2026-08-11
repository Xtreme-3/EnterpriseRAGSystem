# 需求卡片：J1-多轮对话（查询改写 + 历史上下文）

> RAG 进阶。定位：**把单轮问答升级为多轮**——用户追问"那超过一万呢？""再详细点"这类
> 带指代/省略的问法，只有结合上文才能听懂。核心不是"对话"，而是**改写让检索听懂**。
>
> **当前无 LLM API（.env 为 `RAG_PROVIDER=mock`）**：改写不依赖 LLM 语义也能落地——
> 离线用确定性**规则改写**兜底，配置真实大模型后自动升级为 **LLM 改写**。能力先铺好，换真模型零改动。

## 一句话

前端携带最近对话历史 → 后端把当前问句**改写成独立可检索的问句**再检索，同时把历史作为
上下文拼进生成 prompt；无历史时走原单轮路径（零开销、零回归）。

## 背景与动机

B6 建立单轮问答：`rag.ask(kb_id, query)` 直接拿原始 query 检索（`pipeline.py`），
LLM 只见一条孤立问题（`generator.py` 模板只有【资料】+【用户问题】）。多轮场景下
"那它呢？"这类追问直接检索必召回垃圾。README 迭代路线图列为 ⭐⭐⭐ 多轮对话。

不配真实 LLM 也能做：改写需要的只是一次小 LLM 调用，而 Mock 无语义能力 → 用
**规则改写**（指代/短问句拼接最近上文）离线兜底，确定性可测；待配 DashScope/智谱后
自动走 LLM 改写。结构与 I1 Rerank 的 Noop/Mock/Real 三实现同构。

## 方案

- **历史结构**：`ChatTurn{role: "user"|"assistant", content}`；`AskRequest` 加
  `history: list[ChatTurn]`（上限 20 条，前端实际带最近 6 轮）。
- **查询改写**（新 `app/rag/query_rewriter.py`）：
  - 无历史 → 原样返回（零开销，单轮路径完全不变）
  - **规则策略**（默认，mock 或注入）：当前问句"短 / 带指代连词"（那、这、它、再、
    具体、为什么、怎么样、呢等）且历史非空 → `最近一轮用户问句 + " · " + 当前问句`
    作为检索词；否则原样。确定性、离线可测
  - **LLM 策略**（配真模型）：改写 prompt（最近对话 → 输出独立搜索词），strip 输出；
    测试注入 fake llm 验证 prompt 拼接与解析
- **pipeline**（`RagPipeline.ask(kb_id, query, history, ...)`）：
  - `search_query = rewriter.rewrite(query, history)` → **用 search_query 检索**
    （改写只影响检索；答案 prompt 仍用原始 query）
  - `generator.generate(query, hits, history=history)` 拼【对话历史】块
  - `RagAnswer` 加 `rewritten_query`（是否改写 = 与原 query 是否不同；debug/可测）
- **Generator**：`build_prompt(query, chunks, history=None)`，模板在【资料】前加
  【对话历史】块（"用户：… / 助手：…"）；**历史里的助手回答不标引用号、不算资料**。
- **MockLLM 兼容**：`_parse_prompt` 只认【用户问题】与 `[N]` 块，历史块在【资料】前，
  不影响现有解析（回归验证）。
- **前端 Chat.vue**：发送时把 messages 最近 N 轮（排除当前流式消息与 sources 字段）
  作为 `history` 提交；聊天界面无需大改。

## 子块

| 子块 | 功能 | 说明 |
|------|------|------|
| J1-1 | 查询改写器 | `QueryRewriter` 规则/LLM 双策略 + 无历史恒等 |
| J1-2 | pipeline 接入 | `ask` 加 history；检索用改写词、生成用原 query；返回 rewritten_query |
| J1-3 | 历史上下文 | Generator【对话历史】块 + `AskRequest.history` + 前端携带历史 |
| J1-4 | 测试回归 | 改写/prompt/检索词/接口校验/mock 解析不回归 |

## 输入 / 输出

- `ChatTurn{role, content}`；`AskRequest.history: list[ChatTurn]`（默认 []）
- `QueryRewriter.rewrite(query, history) -> str`（无历史恒等）
- `RagPipeline.ask(kb_id, query, history=None, top_k=None, mode=None) -> RagAnswer`
- `RagAnswer.rewritten_query: str`；`Generator.build_prompt(query, chunks, history=None)`

## 验收标准（可测试）

- [x] 无 history → 检索用原 query、生成 prompt 与单轮完全一致（回归）
- [x] 规则改写：短追问/指代 + 历史 → 检索词 = 最近用户问句·当前问句；自包含长问句 → 原样
- [x] LLM 改写：注入 fake llm，改写 prompt 含历史与当前问句，输出被 strip；异常时回退原 query
- [x] ask/ask_stream 带 history → 响应正常 + rewritten_query 正确；非法 role 422；超限 422
- [x] 生成 prompt 含【对话历史】块且顺序正确（历史在前、资料次之）；助手历史不参与引用编号
- [x] MockLLM `_parse_prompt` 解析不回归（历史块不破坏【用户问题】/[N] 提取）
- [x] 现有 153 测试全绿 + 新增 multiturn 测试
- [x] `npm run build` 通过

## 依赖

- 前置：B6（问答链路）、H1（检索）、I1（Rerank，本轮不涉及但 pipeline 共用）
- 复用：`get_settings`、`build_llm`、测试 fixture 钉死 mock/chroma/SQLite、PG 集成测试模式
- 新增：无新依赖

## 不做什么（边界，防止范围蔓延）

- 不做会话持久化 / 新表（多轮刷新即丢由前端承担，会话列表留 **J2**）
- 不做历史 token 级精算截断（先按条数粗截断，上限 20）
- 不做改写结果的用户可见展示（rewritten_query 后端返回即可）
- 不假装 Mock 有语义能力（规则改写独立于 LLM，文档写明仅兜底）

## 失败点 / 风险

- 历史块放错位置破坏 MockLLM 解析 → 历史块必须在【资料】之前，回归测试兜底
- 改写词与生成词混用 → 明确"检索用改写词、答案 prompt 用原 query"，测试验证 rewritten_query
- 规则改写误伤自包含问句 → 长度 + 指代词双重判定，测试覆盖边界
- 前端历史含流式消息导致错位 → 发送前剔除当前 streaming 消息与 sources
