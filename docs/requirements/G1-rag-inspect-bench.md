# 需求卡片：G1-检索质检台（Retrieval Inspection Bench）

> 管理后台方向第一块积木。定位：**RAG 显微镜**——把 chat 接口黑盒掉的检索层暴露出来，
> 输入 query 即可看到系统实际命中了哪些切片、分数多高，诊断"为什么这个问题答得好/不好"。

## 一句话

对知识库输入 query，实时跑向量检索并把 top-k 命中切片可视化（内容 + 来源文档 + 相似度分数），不含 LLM 生成。

## 子块

| 子块 | 功能 | 说明 |
|------|------|------|
| G1-1 | 后端检索接口 `POST /api/kbs/{id}/inspect` | 只跑 `Retriever.retrieve()`，不经过生成 |
| G1-2 | 前端质检台页面 `/kbs/:kbId/inspect` | query 输入 + top_k 调节 + 命中卡片列表 |

## 输入 / 输出

- `POST /api/kbs/{kb_id}/inspect`  body `{"query": "...", "top_k": 5}`
  → `{"query": "...", "hits": [{"document_id": 1, "filename": "xxx.pdf", "chunk_index": 0, "content": "...", "score": 0.87}]}`
- 命中列表按 score 降序，score 保留 4 位小数

## 验收标准（可测试）

- [ ] 未登录访问返回 401
- [ ] 提问不属于当前用户的知识库返回 403
- [ ] 返回结构含 document_id / filename / chunk_index / content / score，**不含 answer**
- [ ] top_k 可调，默认值与 `settings.top_k` 一致，范围限制 1–50
- [ ] 空知识库返回空 hits 列表（200）
- [ ] 前端 `/kbs/:kbId/inspect` 页面可访问，含：KB 名回显、query 输入、top_k 调节、命中卡片（分数条 + 文档名 + 切片内容，内容过长截断）
- [ ] 命中分数可视化排序（同一条命中，分数条越长越靠前）
- [ ] `pytest` 全部通过（含新增 inspect 测试）

## 依赖

- 前置：B6（已有 `Retriever` / 向量库可复用）、F1–F5（前端路由/导航/API 封装结构）
- 复用：B2 `get_current_user`、B3 `_get_user_kb_or_403`、A5/A10 `VectorStore.search`

## 不做什么（边界，防止范围蔓延）

- 不跑 LLM 生成与溯源引用（那是 B6 chat 的职责，质检台只看检索层）
- 不做参数对比 / 多 query 并列对比（后续积木）
- 不做 Rerank 对比调试（Rerank 是 roadmap 进阶项）
- 不做文档健康 / 死文档检测（后续积木，G 系列）
- 不做问答日志埋点 / 溯源热力（依赖日志，另行排期）

## 失败点 / 风险

- 空知识库 / 无命中：返回空 hits，前端给空态提示
- embedding 提供方调用失败：返回 502「检索服务暂时不可用」
- 命中 content 可能很长：前端截断展示 + 展开
- 真实 LLM 供应商不可用时：本积木只用 embedding，mock provider 可离线跑通
