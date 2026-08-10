# 需求卡片：G4-问答日志埋点（Q&A Log）

> 管理后台方向第四块积木。定位：**记录每次问答的全量事实**——query、answer、命中文档集合，
> 既是「问答历史」的用户可见功能，更是后续死文档检测 / 溯源热力（G5+）的共同数据源。

## 一句话

每次问答（非流式 + 流式）落一条日志（用户/知识库/query/answer/命中文档/时间），并提供历史查询接口与页面。

## 子块

| 子块 | 功能 | 说明 |
|------|------|------|
| G4-1 | `QaLog` 模型 | user_id/kb_id/query/answer/hit_doc_ids(JSON)/hit_count/created_at |
| G4-2 | 埋点 | chat.py 的 `ask` 与 `ask_stream` 成功路径写日志 |
| G4-3 | 历史查询 `GET /api/kbs/{id}/qa-logs` | 按时间倒序，limit 可调 |
| G4-4 | 前端问答历史页 `/kbs/:kbId/qa-logs` | 表格：query/命中数/时间，answer 可展开 |

## 输入 / 输出

- 每次 `POST /api/kbs/{id}/ask` 或 `/ask/stream` 成功后，`qa_logs` 表新增一条
- `GET /api/kbs/{kb_id}/qa-logs?limit=20` → `[{"id": 1, "query": "...", "answer": "...", "hit_doc_ids": [1,2], "hit_count": 2, "created_at": "..."}]`
- `hit_doc_ids` 为命中文档 id 去重排序；空命中为 `[]`

## 验收标准（可测试）

- [ ] `qa_logs` 表随 `init_db` 自动创建
- [ ] 非流式 ask 成功后日志落库，含 query/answer/命中文档
- [ ] 流式 ask/stream 成功后同样落库
- [ ] 空命中（空库提问）也记录，hit_doc_ids=[]，hit_count=0
- [ ] 未登录查询历史返回 401
- [ ] 查询他人知识库历史返回 403
- [ ] 历史按时间倒序，limit 生效（默认 20，上限 100）
- [ ] 前端历史页可访问：query/命中数/时间，answer 过长截断可展开
- [ ] 现有 73 测试不回归，新增日志测试全绿

## 依赖

- 前置：B6（ask/ask_stream 端点）、G1–G3（前端结构）
- 复用：`_get_user_kb_or_403`、`get_current_user`、Document/KnowledgeBase 模型

## 不做什么（边界，防止范围蔓延）

- 不做检索命中明细的逐 chunk 快照（只存 document_id 集合，够死文档检测用）
- 不做点赞/反馈（roadmap「反馈日志」是独立进阶项）
- 不做死文档检测 / 溯源热力（依赖本块数据，G5 排期）
- 不做日志清理 / 轮转策略
- 不改 ask/ask_stream 的返回结构与鉴权

## 失败点 / 风险

- 埋点不能影响问答主流程：写日志异常必须吞掉（日志失败不 500）
- `ask_stream` 是 StreamingResponse：埋点需在返回流前同步执行，拿到完整 result 后落库
- hit_doc_ids 存 JSON 文本，查询接口反序列化需防御非法数据
- **FK 策略**：`qa_logs` 对 users / knowledge_bases 用 `ON DELETE CASCADE`——KB 删除时级联清日志。
  理由：现有测试 cleanup 均用 `DELETE FROM knowledge_bases`，无级联会导致 ForeignKeyViolation
  （QA 日志残留引用已删 KB）；产品上 KB 删除后其问答日志也失去意义。
