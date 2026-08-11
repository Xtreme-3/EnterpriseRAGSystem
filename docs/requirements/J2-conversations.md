# 需求卡片：J2-会话持久化（对话记录 + 服务端历史）

> RAG 进阶。J1 做了多轮（前端带 history + 查询改写），但**刷新即丢**、无会话概念。
> J2 把对话存库：会话列表 + 每条问答落库，刷新不丢、可续聊；history 改由**服务端从
> 会话推导**（前端不再需要携带），并为后续点赞/反馈日志预留 message 挂载点。

## 一句话

新增 `conversation`（会话）+ `chat_message`（消息）两张表与 CRUD 接口；`ask`/`ask_stream`
可选携带 `conversation_id` → 服务端从库内历史做改写 + 把本轮 user/assistant 消息落库；
前端加会话侧边栏（新对话/加载历史/删除）。

## 背景与动机

J1 的多轮历史在前端内存里，刷新即丢，且每次都要前端拼 history 上传。
真实使用需要"打开一个知识库 → 看到之前的会话 → 点进去继续聊"。
README 迭代路线图列为 ⭐⭐⭐ 多轮对话；会话持久化是其完整形态，也是后续
点赞/反馈（对某条回答评分）的天然挂载点。

## 方案

- **数据模型**（`app/core/models.py`，新表由 `create_all` 建，无需 ALTER）：
  - `Conversation`：`id`、`kb_id`（FK CASCADE）、`user_id`（FK CASCADE）、
    `title`（默认"新对话"，首条 user 消息时更新为提问前 30 字）、`created_at`、`updated_at`
  - `ChatMessage`：`id`、`conversation_id`（FK CASCADE）、`role`（user|assistant）、
    `content`、`sources`（JSON 文本，assistant 消息存引用来源）、`rewritten_query`、`created_at`
- **会话 CRUD**（`app/api/chat.py`，前缀 `/api/kbs/{kb_id}/conversations`，全部 viewer+ 可访问，
  会话是**用户私有**数据）：
  - `POST` 创建（body 可带 title，默认"新对话"）→ 201 会话
  - `GET` 列表（updated_at 倒序）
  - `GET /{conv_id}` 详情（含按 id 正序的消息列表）
  - `PATCH /{conv_id}` 改名（UI 暂不做，接口预留）
  - `DELETE /{conv_id}` 删除（级联删消息）
  - 归属校验：conv 不存在或 `kb_id` 不符 → 404；`user_id` 非本人 → 403（会话私有）
- **ask 接入**（`AskRequest.conversation_id: int | None`）：
  - 带 `conversation_id` → 校验归属 → **服务端从库内已有消息构建 history**（J1 改写/上下文
    复用同一链路，前端不再传 history）→ `rag.ask(query, history=历史)` → 生成成功后
    **保存本轮 user 消息 + assistant 消息**（sources/rewritten_query 落库）→
    更新 `updated_at`、首条时设 title → 响应带 `conversation_id`
  - 不带 → 维持 J1 行为（前端传 history），完全向后兼容
  - 生成失败（502）→ 不落库，避免半条消息
- **前端 Chat.vue**：左侧会话侧边栏（列表/新对话/删除）；首次发送前若未选会话 →
  `POST` 自动建会话（title=提问前 30 字）→ 后续复用 `conversation_id`；
  选中历史会话 → `GET` 详情加载消息；发送不再携带 history（服务端推导）。

## 子块

| 子块 | 功能 | 说明 |
|------|------|------|
| J2-1 | 数据模型 | ✅ Conversation + ChatMessage（FK CASCADE，create_all 建表） |
| J2-2 | 会话 CRUD | ✅ POST/GET 列表/GET 详情/PATCH 改名/DELETE + 归属校验 |
| J2-3 | ask 落库 | ✅ conversation_id → 服务端历史 + 保存消息 + title/updated_at |
| J2-4 | 前端侧边栏 | ✅ 会话列表/新对话/删除/加载历史，自动建会话 |

## 输入 / 输出

- `POST /api/kbs/{kb_id}/conversations` body `{title?}` → `Conversation{id, kb_id, title, created_at, updated_at}`
- `GET /api/kbs/{kb_id}/conversations` → `[Conversation]`（updated_at 倒序）
- `GET /api/kbs/{kb_id}/conversations/{conv_id}` → `{id, title, created_at, messages: [{id, role, content, sources, rewritten_query, created_at}]}`
- `PATCH /api/kbs/{kb_id}/conversations/{conv_id}` body `{title}`；`DELETE` → `{status}`
- `AskRequest.conversation_id: int | None`；`AskResponse.conversation_id: int | None`

## 验收标准（可测试）

- [x] CRUD：创建 201 → 列表含它 → 详情含消息 → 改名生效 → 删除 200 且再查 404
- [x] 归属：他人会话 403；别库会话 404；未登录 401
- [x] ask 带 conversation_id：首轮返回 conversation_id，落库 user+assistant 消息（assistant 带 sources/rewritten_query）
- [x] 服务端历史：第二轮追问经库内历史改写（rewritten_query 体现上一轮用户问句）
- [x] 标题：创建默认"新对话"；首轮发消息后更新为提问前 30 字
- [x] 消息顺序：详情按 id 正序；列表按 updated_at 倒序（新对话置顶）
- [x] 删除会话级联删消息；不带 conversation_id 的 ask 维持 J1 行为（回归）
- [x] 现有 174 测试全绿 + 新增 conversation 测试
- [x] `npm run build` 通过

## 依赖

- 前置：J1（多轮改写 + 历史上下文）
- 复用：`get_db`、`_get_user_kb_or_403`、`QueryRewriter`/`Generator` 历史链路、PG 集成测试模式
- 新增：无新依赖

## 不做什么（边界，防止范围蔓延）

- 不做会话重命名 UI（PATCH 接口预留）、不做会话归档/搜索/分页（先全量倒序）
- 不做消息编辑/删除单条（整会话删除即可）
- 不做多会话并发切换的乐观锁（前端的活跃会话唯一）
- 不做点赞/反馈（J2 只落 message 数据，评分留后续积木）
- 不改变 J1 无 conversation_id 的显式 history 路径（向后兼容）

## 失败点 / 风险

- 生成失败也落库半条消息 → 先成功再落库（写库放 ask 成功返回之前，失败直接 raise）
- 服务端历史与前端本地消息不同步 → 切换会话强制重新 GET 加载，发送走服务端推导
- FK 级联遗漏导致孤儿消息 → Conversation 删除级联 ChatMessage，测试验证
- title 被长问句撑爆 → 截断 30 字；空问句不建会话（前端校验）
