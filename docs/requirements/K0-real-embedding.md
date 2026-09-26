# 需求卡片：K0-真实 Embedding 接入（供应商插槽解耦）

> 每张卡片 = 一个功能积木。实现前先填好，做完后在 `progress-log.md` 更新状态。
>
> **编号说明**：K0 编号在 K1 之前，但实际动工在 K2 之后。用 K0 表示「K 系列的地基」——
> 它是 K3 的前置解锁项，不是排在 K1 之前的块。

## 一句话

让 `EMBEDDING_PROVIDER` 能独立指向「阿里云百炼官方」，与 LLM 走的第三方中转站彻底分开，
从而把 `EMBEDDING_PROVIDER=mock` 换成**真实语义向量**。

## 为什么这是 K3 的前置阻塞

`docs/roadmap.md`「为什么是这个顺序」第 3 条写着：K3 治的是"内容不符合要求"，
但**前提是能连真 Key 实测**——在 mock 下改 prompt 无法验证效果。

而在此之前 embedding 一直是 mock：

- `MockEmbedding.DIM = 64` 是硬编码的哈希词袋伪向量，`data/chroma` 里 `kb_1` 就是 64 维 mock 产物
- mock 向量**没有语义**，检索质量本身不可评测，K3 的 prompt 改完了也看不出好坏
- H2 的 `structure+semantic` 语义切分同理，一直无法真跑

所以 K0 不是锦上添花，是 K3 的**解锁条件**。

## 输入 / 输出

- 输入：`.env` 的 `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` / `BAILIAN_API_KEY`；
  可选 `RERANK_PROVIDER` / `RERANK` / `RERANK_MODEL`
- 输出：`build_embedding()` 返回真实的 `OpenAICompatEmbedding`（1024 维语义向量）；
  `build_reranker()` 能独立指向与 LLM 不同的供应商

## 供应商选型（2026-09-26 定）

| | **阿里云百炼官方（选定）** | 硅基流动 | 本地推理 |
|---|---|---|---|
| 花钱 | ¥0 | ¥10 一次性 | ¥0 |
| 免费额度 | 每模型 100 万 token / 开通后 90 天 | 无额度；模型单价 0 | 无限 |
| embedding | `text-embedding-v4`（Qwen3-Embedding 系列） | `BAAI/bge-m3`（2024-01） | bge-m3 |
| 维度 | 1024（默认，与现有 `EMBEDDING_DIM` 一致） | 1024 | 1024 |

**选百炼的理由**：① 官方帮助中心白纸黑字给免费额度；② `text-embedding-v4` 属 Qwen3-Embedding
系列，比 2024 年的 bge-m3 新一代——正好回应"bge-m3 可能效果不够好"的疑虑。
免费额度**仅华北2（北京）地域**，compatible-mode 默认端点即北京。

**硅基流动被否的实测依据**：实名认证完成后，**余额 0 时连官方标注免费的 `BAAI/bge-m3`
也返回 `402 {"code":30001,"message":"Sorry, your account balance is insufficient"}`**。
其"免费模型"指**单价 0 元**，而新用户赠额是代金券、**需账户有余额才能抵扣**，故余额 0 → 全部 402。

**为什么不复用现有 `dashscope` 槽位**：本项目的 `dashscope` 槽位历史指向中转站
`tokenrhythm.studio`（`.env` 注释自己承认了）。而 LLM 必须继续留在中转站
（`qwen3.8-flash` 只有中转站有）。同名槽位共用一个 `base_url`，混用会把 LLM 也拽去百炼官方。
故**新开 `bailian` 供应商名**，语义即"阿里云百炼官方"，与 `dashscope`（=中转站）并存。

## 验收标准（可测试，逐条打勾）

- [x] `_resolve_endpoint(settings, "bailian")` 返回百炼的 `(base_url, api_key)`
- [x] `bailian` 缺 key 时抛 `ValueError`，文案含 `BAILIAN_API_KEY`
- [x] `EMBEDDING_PROVIDER=bailian` + `RAG_PROVIDER=dashscope` 时，embedding 走百炼、LLM 仍走中转站（互不串台）
- [x] `RERANK_PROVIDER` 缺省时 rerank 仍跟随 `LLM_PROVIDER`（回归零影响）
- [x] `RERANK_PROVIDER=bailian` 而 LLM 走中转站时，reranker 指向百炼**原生端点**
- [x] 模型返回维度 ≠ `EMBEDDING_DIM` 时抛 `ValueError`，文案含实际维度与"需重建集合"提示
- [x] 未知供应商名抛 `ValueError`
- [x] 全量测试零回归（原 212 条全部不回归，新增用例全绿）

**执行中追加的验收项**（开工后才发现，属本块必要范围）：

- [x] 单测不再依赖开发者本机 `.env`（`tests/conftest.py` 钉死 mock；否则切真实供应商后
      单测会真实联网 + 因维度漂移集体失败）
- [x] 向量集合可从元数据重建，无需原始文档（`scripts/reindex_embeddings.py`）

## 子块状态

| 子块 | 内容 | 状态 |
|---|---|---|
| K0-1 | `config` 加 `bailian_api_key` / `bailian_base_url` / `rerank_provider` | ✅ |
| K0-2 | `factory._resolve_endpoint` 加 `bailian` 分支；`build_reranker` 改用独立槽位 | ✅ |
| K0-3 | `DashScopeRerank`（百炼原生 rerank，兼容层无此接口） | ✅ |
| K0-4 | `OpenAICompatEmbedding` 返回维度自检 | ✅ |
| K0-5 | `tests/conftest.py` 切断单测对 `.env` 的依赖 | ✅ |
| K0-6 | `scripts/reindex_embeddings.py` 向量重建脚本 | ✅ |
| K0-7 | `.env` / `.env.example` 切换 + 数据重建 + 检索实测 | ✅ |

## 实测记录（2026-09-26）

**端点可用性**（`qwen3.7-text-embedding` + 用户提供的百炼 Key）：

| 调用 | 端点 | 结果 |
|---|---|---|
| embeddings | `compatible-mode/v1/embeddings` | ✅ 200，**dim = 1024**，`qwen3.7-text-embedding` |
| embeddings（对照） | 同上，`text-embedding-v4` | ✅ 200，dim = 1024 |
| rerank | `compatible-mode/v1/rerank` | ❌ **404**（兼容层没有 rerank） |
| rerank | `api/v1/services/rerank/text-rerank/text-rerank` | ✅ 200，`gte-rerank-v2` / `qwen3-rerank` 均可用 |

**检索质量实测**（实库 15 切片，hybrid 关闭取纯向量 top-5）：

| 查询 | 类别 | top1 | top5 区间 |
|---|---|---|---|
| 供应商准入需要满足哪些条件 | 库内有 | **0.793** | 0.578 – 0.793 |
| 皮具用什么皮料 | 库内有 | **0.696** | 0.377 – 0.696 |
| 出差住宿能报多少钱 | 库内无 | 0.317 | 0.260 – 0.317 |
| 今天天气怎么样 | 完全无关 | 0.283 | 0.235 – 0.283 |

**结论**：命中与未命中在 top-1 上相差 **0.38**，中间有干净的分隔带（未命中 ≤0.32，
命中 ≥0.70）。这是 mock 从未提供过的语义可分的分数分布——K3 的拒答分级因此有了
量化依据：当前 `similarity_threshold=0.1` 明显偏低，可上调到 **0.35 左右**，
让无关问题直接零命中。**具体阈值属 K3 范围，本块不改。**

## 依赖

- 前置积木：`K2-pipeline-singleton`（provider 已收归 lifespan 装配，改插槽只需改装配处）
- 人工前置：**阿里云账号开通百炼 + 创建 API Key**（免费额度自动发放，无需充值）

## 不做什么（边界，防止范围蔓延）

- 本积木**不做**：改 `dashscope` 槽位名（LLM 链路保持原样，零回归优先）
- 本积木**不做**：`inspect.py` 里请求期 `build_reranker()` 的复用优化（K2 遗留的尾巴，另开卡）
- 本积木**不做**：向量集合的自动迁移。维度从 64 → 1024 **必须重建**，由
  `scripts/reindex_embeddings.py` **手动**执行——重建是有副作用的运维动作，
  不该在应用启动时隐式发生（启动即改数据，回滚成本高）
- 本积木**不做**：调 `similarity_threshold`。实测已给出量化依据（见下方实测记录），
  但改阈值属于 K3 的拒答分级范围
- 本积木**不做**：真实端到端检索质量评测（需 key 到手后跑，属 K3 的验收范围）

## 失败点 / 风险

- **维度不匹配是静默的**。向量库按 `embedding.dim` 建集合，而 `OpenAICompatEmbedding.embed()`
  原先**不传 `dimensions`**、只靠模型默认维度，`self._dim` 纯记账。模型默认维度与配置不一致时
  会写出错误长度的向量 → 表现为"检索查得到但永远不相关"或向量库底层异常。
  **处置**：本次给 `embed()` 加返回维度自检，不符即抛带修复指引的 `ValueError`。
- **换维度必须重建集合**。`data/chroma` 里 `kb_1` 是 64 维 mock 产物，改 1024 维后不清库会持续报错。
  **处置**：写进 `.env` 注释与切换步骤。
- **免费额度 90 天到期**。100 万 token 够本项目用很久（≈200 万汉字），但到期后需关注。
  **处置**：卡片留档，不作为本次实现项。
- **rerank 曾跟随 LLM 槽位**。`build_reranker` 原先取 `s.llm_provider or s.rag_provider`，
  embedding 换百炼后 rerank 会被打到**没有 rerank 接口的中转站**上。
  **处置**：新增 `rerank_provider` 字段，缺省仍跟随 LLM 槽位（回归零影响）。
