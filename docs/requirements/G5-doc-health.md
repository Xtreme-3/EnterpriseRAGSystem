# 需求卡片：G5-文档健康分析（Doc Health）

> 管理后台方向第五块积木。定位：**基于 G4 问答日志，回答"这份知识库里哪些文档在被问答真正使用，哪些从没被命中"**。
> 死文档清单给出可直接执行的清理/重整理建议；命中热力揭示内容分布。复用 G4 的 `qa_logs.hit_doc_ids`，零新增采集成本。

## 一句话

聚合 KB 的问答日志，输出每个文档的历史命中次数与最近命中时间：从未命中的归入死文档清单（带状态/切片上下文），命中的按热度排序展示 top N。

## 子块

| 子块 | 功能 | 说明 |
|------|------|------|
| G5-1 | `GET /api/kbs/{id}/doc-health` 接口 | 聚合 `qa_logs.hit_doc_ids` → 每文档命中次数 + 最近命中时间 |
| G5-2 | 死文档清单 | hit_count=0 的文档，含 status/chunk_count/error（未就绪 vs 已索引真死角由前端区分） |
| G5-3 | 命中热力 top N | hit_count 降序，默认 10，上限 50 |
| G5-4 | 前端页面 `/kbs/:kbId/doc-health` | summary 统计卡 + 死文档表格 + 热力排行 |

## 输入 / 输出

- `GET /api/kbs/{kb_id}/doc-health?limit=10` → 鉴权后返回：

```json
{
  "kb_id": 1,
  "summary": {
    "total_documents": 5,
    "indexed": 3,
    "active_count": 1,
    "dead_count": 2,
    "total_questions": 4,
    "hit_rate": 0.2
  },
  "dead_docs": [
    {"id": 3, "filename": "old.md", "status": "indexed", "chunk_count": 5, "hit_count": 0, "error": null}
  ],
  "hot_docs": [
    {"id": 1, "filename": "policy.txt", "hit_count": 4, "last_hit_at": "2026-08-07T12:00:00"}
  ]
}
```

- `hit_rate = active_count / total_documents`（0.0–1.0，保留 2 位；无文档时为 0）
- 同一问答日志对某文档只计一次（`hit_doc_ids` 已去重），"命中次数"= 该文档被问答命中的次数
- 未就绪文档（failed / pending / processing）若从未命中同样进入 `dead_docs`，保留 `status`/`error` 供前端标注"未就绪"

## 验收标准（可测试）

- [ ] 未登录访问返回 401
- [ ] 查询他人知识库返回 403
- [ ] 空知识库：summary 全 0、hit_rate=0、dead_docs/hot_docs 为空
- [ ] 上传文档 + 问答命中后：该文档进 `hot_docs`，hit_count 随问答次数累加、last_hit_at 更新
- [ ] 从未被命中的文档进 `dead_docs`，hit_count=0，带 status/chunk_count
- [ ] failed 文档也归 `dead_docs` 且保留 error 文本
- [ ] `limit` 控制 hot_docs 条数（默认 10，上限 50，非法值报 422）
- [ ] 与 query 无关的文档（相似度低于阈值）不计入命中：`SIMILARITY_THRESHOLD` 生效，死文档判定不被噪声污染
- [ ] 现有 79 测试不回归，新增文档健康测试全绿

## 依赖

- 前置：B6（ask/ask_stream）、G4（qa_logs 落库 + hit_doc_ids）
- 复用：`_get_user_kb_or_403`、`get_current_user`、Document/QaLog 模型、`_parse_doc_ids`

## 不做什么（边界，防止范围蔓延）

- 不做命中率阈值/告警（"死文档比例超过 X 提醒"留给后续运营规则）
- 不做清理动作本身（死文档只标记，删除走现有 `DELETE /api/documents/{id}`）
- 不做死文档自动重摄取 / 再向量化
- 不做逐 query 的未命中归因（"哪些问题问不到这份文档"——检索质检台 G1 已覆盖单 query 视角）
- 不把统计做成增量/定时任务（每次请求全量聚合，V1 数据量足够小）

## 失败点 / 风险

- `hit_doc_ids` 存 JSON 文本：反序列化必须防御非法数据（复用 G4 的 `_parse_doc_ids`）
- 聚合需保证 `last_hit_at` 正确：遍历日志需按 `created_at` 升序，后写覆盖
- 日志可能引用已删除文档（历史遗留）：聚合时对不存在于文档列表的 doc_id 直接跳过，不报错
- 命中文档计数口径：`hit_doc_ids` 本身去重，一条日志对一文档 +1，避免被多 chunk 命中虚增
- 空知识库 / 无问答记录时要正常返回 200（不 500），hit_rate 不除零
