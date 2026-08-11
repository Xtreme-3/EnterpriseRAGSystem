# 需求卡片：I1-重排序（Rerank）

> RAG 进阶。定位：**精排**——H1 混合检索解决"召回"（捞回向量漏掉的关键词命中），
> rerank 解决"排序"（用交叉编码/词重叠等更强的相关性信号，把 top-k 候选重排一遍，
> 让最相关的切片排最前，喂给 LLM 与溯源展示）。

## 一句话

检索出 top-k 候选后，用独立的 rerank 模型（或离线词重叠打分）对候选重新按相关性排序，
替换检索分作为最终命中分数；质检台可开关并对比"重排前 vs 重排后"。

## 背景与动机

当前 pipeline 的 `Retriever.retrieve()` 排序依据是 **双编码向量相似度 / 混合融合分**，
对候选缺乏"精准精排"：近似相关（检索）vs 强相关（如 query 关键词高度重叠、交叉编码
给出的高置信分数）区分度不足。`app/rag/reranker.py::Reranker` 与 `RerankProvider`
接口在 A9 阶段已预留，`build_reranker` 当前恒返回 `NoopRerank`（注释：阶段二接入
gte-rerank / 智谱 rerank）。本积木把该插槽做实。

## 方案

- **RerankProvider 接口改造**：`rerank(query, texts, scores) -> list[float]`
  （原 `(query, texts)`）。新增 `scores`（原始检索分，与 texts 对齐）——
  真实重排模型可用它兜底/混合，Noop 靠它做恒等。
- **三个实现**：
  - `NoopRerank`（默认，不重排）：`rerank` 原样返回 `scores`，排序与分数完全不变 → 回归 0 影响。
  - `MockRerank`（离线可测）：`query` 分词（复用 `app/rag/hybrid.tokenize`）对每条候选内容
    做**词重叠率**打分 `overlap / len(terms)` ∈ [0,1]，确定性、无网络；无检索词时回退原分。
  - `OpenAICompatRerank`（真实）：`POST {base_url}/rerank`，body
    `{"model": rerank_model, "query": ..., "documents": [...]}`，解析
    `results[].relevance_score`（按 index 对齐，兼容 `score` 字段）。DashScope gte-rerank 走
    `compatible-mode/v1/rerank`，与现有 base_url 天然契合；httpx 直连（openai SDK 无 rerank 方法）。
- **配置**（`app/config.py`）：
  - `rerank: bool = False` —— 总开关，默认关（不引入额外延迟/成本，回归 0 影响）
  - `rerank_model: str = "gte-rerank"` —— 真实模型名
- **装配**（`build_reranker`）：`rerank=False → NoopRerank`；`mock → MockRerank`；
  真实供应商 → `OpenAICompatRerank`。
- **分数语义**：`Reranker.rerank` 用 `dataclasses.replace` 把命中的 `score` 更新为
  **重排分**（替代检索分），保证排序与分数一致；溯源 sources、质检分数条随之反映重排结果。
- **质检台对比**：inspect 请求可传 `rerank` 覆盖（`null` 用服务端配置）；响应新增
  `rerank` 标志位 + 每条命中的 `pre_score`（重排前检索分），前端画"检索/重排"双分数条。
- **chat 链路**：`RagPipeline.ask` 已接 `Reranker`，无需改；`Settings.rerank=True` 时
  问答自动重排（sources 分数=重排分）。

## 子块

| 子块 | 功能 | 说明 |
|------|------|------|
| I1-1 | 接口 + Noop + Mock | `rerank(query, texts, scores)`；MockRerank 词重叠，离线可测 |
| I1-2 | 真实供应商 | `OpenAICompatRerank`（{base}/rerank + results 解析），解析逻辑可单测 |
| I1-3 | 装配 + 配置 | `rerank`/`rerank_model` 配置，`build_reranker` 按配置返回 |
| I1-4 | 分数接线 | `Reranker.rerank` 用 replace 更新 score，pipeline/溯源一致 |
| I1-5 | inspect 对比 | 请求 `rerank` 开关，响应 `rerank` + hit `pre_score` |
| I1-6 | 前端开关 | 质检台「是否重排」开关 + 检索/重排双分数条 |

## 输入 / 输出

- `Settings.rerank: bool`（默认 False）、`Settings.rerank_model: str`（默认 gte-rerank）
- `RerankProvider.rerank(query, texts, scores) -> list[float]`
- `Reranker.rerank(query, chunks, top_n=None) -> list[ScoredChunk]`（已更新 score）
- `POST /api/kbs/{id}/inspect` 请求可选 `"rerank": bool`，响应新增 `"rerank": bool`、
  每条 hit 新增 `"pre_score": float`
- chat（ask/ask_stream）sources 分数在 `Settings.rerank=True` 时为重排分

## 验收标准（可测试）

- [x] `NoopRerank`：返回原分，`Reranker.rerank` 排序与分数完全不变（回归）
- [x] `MockRerank`：词重叠率打分 ∈ [0,1]，确定性；无检索词回退原分
- [x] `Reranker.rerank`：按重排分降序 + `top_n` 截断 + 分数被更新
- [x] `OpenAICompatRerank`：解析 `results[].relevance_score` 按 index 对齐（含 `score` 字段兼容），HTTP 错误抛异常
- [x] `build_reranker`：`rerank=False→Noop`；`mock+true→Mock`；真实供应商→OpenAICompat（无 key 抛明确错误）
- [x] inspect：省略 `rerank` 用服务端配置（默认 False，`pre_score == score`）；`rerank=true` 时返回 `rerank=true` 且命中分数=重排分、`pre_score`=检索分
- [x] inspect 空库/无命中仍 200；非法 rerank 布尔值 FastAPI 校验
- [x] `pytest` 全部通过（含新增 rerank 测试）：**140 全绿**

## 依赖

- 前置：A9（Reranker 插槽）、H1（tokenize 复用、inspect mode 模式）
- 复用：`ScoredChunk`、`dataclasses.replace`、mock 供应商、测试 fixture 钉死 chroma
- 新增依赖：httpx（FastAPI/TestClient 已带，无需新装）

## 不做什么（边界，防止范围蔓延）

- 不做 rerank 模型本地部署/微调（BGE-reranker 本地跑放后续）
- 不做 zhipu rerank 专用适配（其 API 与 DashScope 兼容模式字段可能不同；若同为
  `/rerank`+`relevance_score` 则天然可用，否则留待后续）
- 不做 chat 接口的重排开关透传（chat 用服务端 `Settings.rerank` 全局配置，切换只在质检台）
- 不做 rerank 分数与检索分的加权混合配置（本积木固定"替换"；混合策略后续可加 `fuse` 参数）
- 不改 sources 响应结构（分数语义变为重排分，字段不变）

## 失败点 / 风险

- `{base_url}/rerank` 路径假设 DashScope 兼容模式路径，供应商改路径会 404 → 请求异常向上抛，
  由 chat 502 / inspect 502 兜底，报错信息带供应商名
- `results` 缺 `index` 或字段缺失 → 解析按序兜底，缺分默认 0.0，不 500
- 真实 rerank 模型返回分数可能与相似度量纲不同（如 0-4 分）→ 前端分数条钳制 [0,1]，
  质检台对比只看相对大小；生产观察后决定是否归一
- mock 模式下重排纯词重叠，可能把语义相关但无词重叠的切片排后 → 仅离线演示用途，
  真实模型/评分才准，卡片内注明
