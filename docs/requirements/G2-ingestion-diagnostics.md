# 需求卡片：G2-摄取诊断面板（Ingestion Diagnostics）

> 管理后台方向第二块积木。定位：**知识库体检报告**——把摄取流水线的健康状态摊开，
> 一眼看出哪个文档摄入失败、为什么失败、切片分布是否正常。

## 一句话

对知识库做体检：文档健康度总览（状态分布 + 总切片数）、失败原因聚合（按 error 归类）、异常文档清单。

## 子块

| 子块 | 功能 | 说明 |
|------|------|------|
| G2-1 | 后端诊断接口 `GET /api/kbs/{id}/diagnostics` | 纯元数据查询，不动 vector_store |
| G2-2 | 前端诊断页 `/kbs/:kbId/diagnostics` | 统计卡片 + 失败原因 + 异常清单 |
| G2-3 | KB 卡片入口 | 补「体检」「检索质检」两个入口按钮 |

## 输入 / 输出

- `GET /api/kbs/{kb_id}/diagnostics`（登录 + 归属校验）
  → `{"kb_id": 1, "summary": {"total_documents": 10, "indexed": 8, "failed": 2, "processing": 0, "pending": 0, "total_chunks": 145},
      "failures": [{"error": "文档解析后无有效内容", "count": 1, "documents": [{"id": 3, "filename": "a.pdf"}]}],
      "anomalies": [{"id": 5, "filename": "b.txt", "status": "failed", "chunk_count": 0, "error": "..."}]}`
- `failures` 按 error 文本分组聚合；`anomalies` = failed 文档 + chunk_count==0 的非 pending 文档

## 验收标准（可测试）

- [ ] 未登录访问返回 401
- [ ] 查询不属于当前用户的知识库返回 403
- [ ] 空知识库返回全 0 summary、空 failures / anomalies（200）
- [ ] summary 各状态计数与文档实际状态一致，total_chunks 为 chunk_count 之和
- [ ] 有 failed 文档时 failures 按 error 聚合，含 count 与涉及文档（id/filename）
- [ ] anomalies 包含 failed 文档与零切片文档
- [ ] 前端 `/kbs/:kbId/diagnostics` 页面：统计卡片 + 失败原因 + 异常表格
- [ ] KB 卡片新增「体检」「检索质检」入口，均可跳转
- [ ] `pytest` 全部通过（含新增 diagnostics 测试）

## 依赖

- 前置：G1（前端导航/API 结构）、B4/B5（文档上传与状态机）
- 复用：Document 模型、`_get_user_kb_or_403`

## 不做什么（边界，防止范围蔓延）

- 不做死文档检测（"从未被检索命中"需要问答日志埋点，留待 G 系列后续）
- 不做向量一致性检查（元数据 chunk_count vs 向量库实际向量数，需扩展 VectorStore 接口，后续积木）
- 不做时间趋势 / 图表（后续）
- 不做自动修复 / 重试摄取

## 失败点 / 风险

- failed 文档可能没有 error 文本（异常路径差异）→ failures 按 `error or "未知错误"` 归并
- 大量文档时分页聚合需在 SQL 层做，避免前端渲染卡顿（本积木直接全量聚合，数据量小）
- 测试中制造 failed 文档：上传"全空白内容"的 txt → 解析后无有效内容 → 状态机置 failed
