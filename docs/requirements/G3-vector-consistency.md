# 需求卡片：G3-向量一致性检查（Vector Consistency Check）

> 管理后台方向第三块积木。定位：**补上 G2 的盲区**——G2 只体检元数据层（状态机），
> G3 对比"元数据 chunk_count"与"向量库实际向量数"，揪出两层之间的静默漂移。

## 一句话

对比元数据层与向量库层的文档切片计数，报告三类不一致：索引缺失 / 数量漂移 / 孤儿向量。

## 子块

| 子块 | 功能 | 说明 |
|------|------|------|
| G3-1 | `VectorStore` 接口加 `document_counts(kb_id) -> dict[int, int]` | 两个实现：pgvector SQL GROUP BY、chroma get+聚合 |
| G3-2 | 诊断逻辑扩展 | G2 的 `GET /api/kbs/{id}/diagnostics` 加 `consistency` 区块 |
| G3-3 | 前端体检页加"向量一致性"区块 | 一致→绿色提示；有漂移→问题表格 |

## 输入 / 输出

- `GET /api/kbs/{kb_id}/diagnostics`（G2 接口扩展）→ 新增区块：
  `"consistency": {"checked": true, "issues": [
      {"kind": "missing_index", "document_id": 3, "filename": "a.pdf", "meta_count": 12, "vector_count": 0},
      {"kind": "orphan_vector", "document_id": 99, "filename": null, "meta_count": 0, "vector_count": 3}]}`
- kind 语义：
  - `missing_index`：meta > 0，向量库 0（摄取"假成功"）
  - `count_drift`：两侧都 > 0 但数量不等（部分写入/重复写入）
  - `orphan_vector`：向量库有、meta 无（删除残留）

## 验收标准（可测试）

- [ ] `VectorStore.document_counts` 抽象方法存在，两个实现可用
- [ ] pgvector：add 多个文档 → document_counts 分组计数正确；delete_collection 后为空
- [ ] chroma：add → document_counts 计数正确（smoke 覆盖）
- [ ] 正常摄取后 consistency.checked=True 且 issues 为空
- [ ] 手动从向量库删掉某文档向量 → 报 missing_index
- [ ] 向量库残留无 meta 的向量 → 报 orphan_vector
- [ ] 向量库不可达时 consistency.checked=False，不拖垮体检接口（200 兜底）
- [ ] 前端体检页显示一致性区块（一致提示 / 问题表格带 kind 中文标签）
- [ ] `pytest` 全部通过

## 依赖

- 前置：G2（诊断接口与页面）、A5/A10（VectorStore 接口双实现）
- 复用：`build_vector_store`、Document 模型

## 不做什么（边界，防止范围蔓延）

- 不做向量内容逐值比对（成本高、收益低，只对数量）
- 不做自动修复 / 重建向量（要动写入逻辑，留 G 系列后续）
- 不做大库性能优化（chroma `get()` 全量拉取，接受中小规模）
- 不改 ingest_file 写入逻辑（只读检查）

## 失败点 / 风险

- **接口改动是核心面**：抽象方法加后两个实现必须同步实现，否则运行期 AttributeError——双实现 + 双测试兜底
- chroma `collection.get(where=...)` 在空集合/无 metadata 时字段为空，需防御
- pgvector 表未建（从未摄取过）时查询抛 UndefinedTable → 捕获返回空，避免体检 500
- 向量库整体不可达（连接失败）→ 捕获置 checked=False，体检仍返回 200
