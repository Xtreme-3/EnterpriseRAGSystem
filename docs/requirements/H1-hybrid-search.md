# 需求卡片：H1-混合检索（Hybrid Search）

> RAG 进阶第一块。定位：**召回补强**——纯向量检索对精确词/型号/代码/专有名词不敏感，
> 混合检索叠加关键词（全文）信号，把这些向量容易漏掉的命中捞回来。

## 一句话

检索时同时跑向量相似度 + 关键词全文匹配，融合两路结果排序，提升精确词/型号/代码的召回率。

## 背景与动机

当前检索只走 `VectorStore.search`（余弦相似度）。问题：

- 型号/代码/专有名词在 embedding 语义空间里区分度差，向量可能给低分被阈值过滤
- 精确关键词命中（如 "GB 4806"、"GPT-4"、"A12"）是最可靠的相关性信号，但向量没用上

A10 选 pgvector 时预留了「原生全文检索」这条路径（README 迭代路线图 ⭐⭐⭐ 混合检索）。

## 方案

- **关键词检索（lexical）**：
  - pgvector：`vectors` 表新增 `content_tokens`（Python 分词器的词序列），建
    `to_tsvector('simple', content_tokens)` 的 **GIN 表达式索引**，查询用 `to_tsquery` + `ts_rank`。
    `'simple'` 配置无停用词，适合中英混排/型号/代码。
  - chroma（离线/开发兜底）：按词逐个 `where_document $contains` 过滤，按词频打分。
- **分词器**（`app/rag/hybrid.py::tokenize`）：英文/数字词整词保序 + 中文串切重叠 **2-gram**
  （无第三方依赖，兼顾中英与型号代码）。
- **融合**（`app/rag/hybrid.py::fuse_hybrid`）：**加权归一化融合**
  `final = vector_weight * vec_score + (1 - vector_weight) * lex_norm`
  - `lex_norm` = 该词命中池内 `lex_score / max_lex_score`（词频/ts_rank 无量纲，池内归一）
  - 向量分保持绝对相似度（前端分数条已钳制 [0,1]）
  - 一路为空则回退另一路；两侧都空返回空

## 子块

| 子块 | 功能 | 说明 |
|------|------|------|
| H1-1 | 分词器 + 融合函数 | `tokenize` / `fuse_hybrid`，离线可测 |
| H1-2 | pgvector 全文检索 | `content_tokens` 迁移 + GIN 索引 + `search_lexical`（ts_rank） |
| H1-3 | chroma 关键词兜底 | `search_lexical`（$contains 词频），离线自包含 |
| H1-4 | 检索器 + API 接入 | `Retriever.retrieve` 按 `retrieval_mode` 走混合；inspect 返回 `mode` |

> 前端切换开关（inspect/对话页可选 mode）已在「前端 mode 切换」子块完成。

## 输入 / 输出

- `Settings.retrieval_mode`：`vector | hybrid`（默认 `hybrid`）
- `VectorStore.search_lexical(kb_id, query, top_k) -> list[ScoredChunk]`
- `Retriever.retrieve(kb_id, query, top_k, mode=None)`：`mode=None` 用配置默认
- `POST /api/kbs/{id}/inspect` 响应新增 `"mode": "hybrid" | "vector"`，`hits[].score` 为融合分

## 验收标准（可测试）

- [x] `tokenize`：中文切 2-gram、英文/数字词保序、去重、单字符丢弃、空串返回 []
- [x] `fuse_hybrid`：两侧都有→融合排序；仅向量→原样；仅词→原样；都空→[]；结果分 ∈ [0,1]
- [x] pgvector `search_lexical`：含关键词的切片可命中（即便向量相似度低），ts_rank 降序
- [x] pgvector 迁移幂等：表已建时 ALTER 加列 + 回填，不重复建索引
- [x] chroma `search_lexical`：含词切片按词频排序命中，空词返回 []
- [x] `retrieval_mode="vector"` 时行为与 H1 之前完全一致（回归）
- [x] inspect 接口返回 `mode`；空库/无命中仍 200 空列表
- [x] chat（ask/ask_stream）接口透传 `mode`，非法值 422
- [x] 前端切换开关：inspect 质检台 + 对话页可切换 vector/hybrid（后端 `hybrid` 默认）
- [x] `pytest` 全部通过（含新增 hybrid 测试）：**109 全绿**

## 依赖

- 前置：A10（pgvector 存储）、A9（Retriever）、B6/G1（chat/inspect API）
- 复用：`VectorStore` 接口、`ScoredChunk`、mock embedding（离线可测）、测试 fixture 钉死 chroma

## 不做什么（边界，防止范围蔓延）

- 不做 RRF 与加权融合的对比实验（本积木固定加权归一化，后续可加 `fuse_method` 配置）
- 不做中文分词器（jieba 等）接入，2-gram 够用且零依赖；精确中文切词后续再排
- 不做前端 mode 切换 UI（后续子块）
- 不改 chat 返回结构（sources 结构与 B6 保持一致，只是分数含义变为融合分）
- 不做 BM25 完整实现（ts_rank 近似，够用）

## 失败点 / 风险

- `to_tsquery('simple', ...)` 对空/非法词会报错 → 词为空返回 []，词序列只含安全 token
- 中文连续串切 2-gram 会引入噪声（如「人工」），靠向量分 + 融合权重压制
- chroma `$contains` 逐词查询在超大库会慢 → 仅离线/开发路径，生产走 pgvector
- 已存在 `vectors` 表缺列 → ALTER + 回填迁移，幂等
