# 需求卡片：B6-流式问答 + 溯源 API

## 一句话
对知识库提问，RAG 检索→重排→生成答案，非流式/SSE 流式双模式，返回答案+引用来源。

## 子块

| 子块 | 功能 | 说明 |
|------|------|------|
| B6-1 | 非流式问答 `POST /api/kbs/{id}/ask` | 全量返回 answer + sources |
| B6-2 | SSE 流式问答 `POST /api/kbs/{id}/ask/stream` | token 级流式推送 + 末尾 sources 事件 |
| B6-3 | 溯源来源返回 | 每条 source 含 filename/chunk_index/content/score |

## 输入 / 输出

- `POST /api/kbs/{kb_id}/ask` → `{"query": "..."}` → `{"query": "...", "answer": "...", "sources": [{"filename": "...", "chunk_index": 0, "content": "...", "score": 0.95}]}`
- `POST /api/kbs/{kb_id}/ask/stream` → SSE 事件流：`token` 事件（逐片推送答案）→ `sources` 事件（来源列表）→ `[DONE]`

## V1 不做什么

- 不做多轮对话（无对话历史）
- 不做 LLM 层流式（当前 mock 完整答案后客户端分片推送，效果等效）
- 不做流式状态码（重试/中断）

## 验收标准（可测试）

- [ ] 非流式问答返回 answer 和 sources
- [ ] sources 包含 filename/chunk_index/content/score
- [ ] 空知识库返回"未找到相关信息"
- [ ] 提问别人的知识库返回 403
- [ ] SSE 流式端点返回 text/event-stream
- [ ] 流式输出 token 事件 + 结尾 sources + [DONE]
- [ ] `pytest` 全部通过

## 依赖

- 前置：B4（文档上传），需要知识库中有已索引的文档
- 复用：A9 `RagPipeline.ask()`
