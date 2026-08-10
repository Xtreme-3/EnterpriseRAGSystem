# 需求卡片：H2-语义切分（Semantic Chunking）

> RAG 进阶第二块。定位：**块质量升级**——定长 800 字切块会把一个完整语义单元拦腰截断，
> 检索命中"半个主题"，回答缺上下文。语义切分让"块自身就是完整主题"，不加父块、不加每次问答成本。

## 一句话

切块不再按固定字符数，而是按文档自身的语义边界（标题/编号条款/段落）切，每块是一个完整主题；
可选在超长节内叠加句级 embedding 相似度微调，进一步在主题断层处细分。

## 背景与动机

- 当前 chunker（A7）是递归定长（默认 800 字，overlap 120），长章节会被硬切在语义中间
- 检索命中的是"片段"，LLM 拿到的是半个主题 → 回答可能缺上下文
- small-to-big（小块检索 + 完整父块回答）会放大每次问答的 LLM token 成本 —— **成本优先否决**（见 2026-08-10 讨论）
- 语义切分把成本放在"上传时一次"而非"每次问答"，且不引入父块冗余

## 方案

- **结构优先切分（`structure`，离线免费）**：
  - 以 `\n\n` 段落为基本单元（解析器 `_normalize` 已保证段落隔离）
  - 标题行判定（启发式，只认可靠形态，降低误判）：
    - Markdown 标题：`^#{1,6}\s+`
    - 章节条款：`^第X章/节/条`（排除紧跟句末标点的情况，如"第1段。"）
    - 编号条款：`^\d+(\.\d+)*\s*[一-鿿]`（如 `1 适用范围`、`4.2 五金件标准`）
  - 标题开启新块，后续非标题段落并入，直到下一个标题 → 每块 = 一个完整小节
  - 超长块（>chunk_size）回退现有递归切分（段落→句子边界优先）
  - 过短块并入相邻块（上限 chunk_size 兜底），避免只有标题的空块
- **句级语义微调（`structure+semantic`，可选，需真实 embedding 才有意义）**：
  - 仅对超过 chunk_size 的结构块，按相邻句子 embedding 余弦相似度找主题断层（低于阈值切）
  - `embed` 以可注入函数传入（`Callable[[list[str]], list[list[float]]]`），逻辑离线可测；
    生产接 `EmbeddingProvider.embed`
  - mock embedding 是哈希词袋伪向量，语义微调不承诺效果，只保证不崩
- **配置**：`CHUNK_STRATEGY`：`fixed`（默认，回归兜底）| `structure` | `structure+semantic`；
  `SEMANTIC_BREAK_THRESHOLD` 句间相似度阈值（默认 0.65，低于则切）

## 子块

| 子块 | 功能 | 说明 |
|------|------|------|
| H2-1 | `split_structure` | 结构优先切分，离线可测 |
| H2-2 | `split_semantic` | 结构 + 句级 embedding 微调，embed 可注入 |
| H2-3 | `Settings.chunk_strategy` / `semantic_break_threshold` + `.env.example` | 配置分流 |
| H2-4 | `IngestionPipeline.ingest_file` 按策略分流 + 测试 | 接入 |

## 输入 / 输出

- `split_structure(text, chunk_size, chunk_overlap) -> list[str]`
- `split_semantic(text, embed, threshold, chunk_size, chunk_overlap) -> list[str]`
- `Settings.chunk_strategy: "fixed" | "structure" | "structure+semantic"`（默认 `fixed`）
- `Settings.semantic_break_threshold: float = 0.65`

## 验收标准（可测试）

- [x] `split_structure`：Markdown `#` / 编号条款标题的文档按小节切块，标题与其内容同块
- [x] `split_structure`：普通段落文档退化为近似 fixed 行为，内容无丢失
- [x] `split_structure`：超长节按段落/句子边界再切，块不超长；过短块合并，无空块
- [x] `split_structure`：空串/短文本 → `[]`/单块
- [x] `split_semantic`：注入假 embed（同主题相似度高、异主题低），低相似度断层切开、高相似度不切、阈值控制松紧
- [x] 配置：`chunk_strategy` 校验三值；`ingest_file` 按策略分流（structure 接入可跑通）
- [x] 回归：默认 `fixed` 行为不变，`pytest` 全部通过：**122 全绿（+13 语义切分测试）**

## 依赖

- 前置：A7（chunker）、A8（摄取流水线）
- 复用：`_collapse` / `_split` / `_apply_overlap`、`EmbeddingProvider`、mock embedding
- 不引入第三方分词/embedding 依赖

## 不做什么（边界）

- 不做 small-to-big（父块）——成本优先否决
- 不做 LLM 参与的切分（贵且慢）
- 不迁移已有文档向量：换策略需**重新摄取**文档才生效（进度日志说明）
- 结构/语义切分在语义边界切块，**不做跨块字符重叠**（`CHUNK_OVERLAP` 仅对 `fixed` 生效），避免上一块的尾字污染下一块主题
- 不做标题语义分类（哪些标题合并成父章节）——本期只保证"标题=块起点"
- 不承诺 `structure+semantic` 在 mock embedding 下的效果（需真实 embedding 验证，后续配 key 后做）

## 失败点 / 风险

- 标题判定启发式可能误判/漏判 → 只认可靠形态（# / 第X章 / 编号条款），无结构信号时退化为递归切分
- 中文标题形态多（"总则"、"第一章"、"一、" 等）→ 本期覆盖常见形态，其余当普通段落
- 切块变化 → 已有索引基于旧切片，换策略后需重新摄取（上传新文档或删了重传）
- mock embedding 句级相似度不准 → `structure+semantic` 逻辑离线可测、效果留待真实 key
