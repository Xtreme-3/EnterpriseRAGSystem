# Enterprise RAG System

**把散在共享盘里的企业文档，变成一个答得准、说得清、断网也能跑的知识库问答服务。**

一条提问走完全程：文档上传 → 解析 → 切片 → 向量化 → 检索（向量 + 关键词双路）→ 重排 → 流式生成 → **每条答案都能点回原文**。

> **187** 个测试用例全绿 · **24** 个 HTTP 端点 · **10** 个前端页面 · **4** 种文档格式 · 全链路可离线运行

---

## 一、为什么做这个

企业内部知识库的真实困境不是"没有数据"，而是**数据取不出来**：文档散在共享盘、格式五花八门、新人只能问老人。直接拿通用大模型问答会撞上两堵墙——

| 问题 | 本系统的应对 |
|---|---|
| **幻觉**：模型编造一个不存在的制度条款 | 答案强制带来源引用（文件名 + 切片序号 + 相似度），点击回到原文切片 |
| **数据出不去**：企业内部文档不能发到公网 API | 供应商层可插拔，`mock` 供应商让全链路**完全离线**跑通；换私有化模型只需改 `.env` |
| **检索是黑盒**：答错了不知道卡在哪一环 | 四个可观测面板：检索质检台 / 摄取诊断 / 问答日志 / 文档健康 |

这不是一个"调通 API 就完事"的 demo。检索质量问题要在**切片策略、检索融合、重排**三个环节上解决，系统把这三处都做成了可配置、可对比、可验收的能力。

## 二、系统架构

```text
┌────────────────────────────────────────────────────────────────────────────┐
│                前端  ·  Vue3 + Vite + Element Plus + Pinia                 │
│                 登录 · 知识库管理 · 文档上传 · 对话(流式)                  │
│           质检台 · 摄取诊断 · 问答日志 · 文档健康                          │
└────────────────────────────────────────────────────────────────────────────┘
                                       │  HTTPS + JWT
                                       ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                             API 层  ·  FastAPI                             │
│         鉴权 JWT · RBAC 角色门槛 · 知识库 · 文档 · 问答(SSE 流式)          │
│      检索质检 · 摄取诊断 · 向量一致性 · 问答日志 · 文档健康                │
└────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                                  核心引擎                                  │
│    摄取  解析(PDF/DOCX/MD/TXT) → 切片(fixed / structure / 语义) → Embedding│
│       问答   检索(向量 + 关键词混合) → Rerank → 生成 LLM + 溯源引用        │
└────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                                   数据层                                   │
│   元数据   SQLite（默认）/ PostgreSQL   ·  User / KB / Document / QaLog    │
│  向量库   ChromaDB（默认）/ pgvector   ·   每个知识库 = 独立集合 kb_{id}   │
└────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌────────────────────────────────────────────────────────────────────────────┐
│                            模型供应商（可插拔）                            │
│  通义千问 DashScope  ·  智谱 GLM  ·  重排 gte-rerank  ·  mock（离线可测）  │
└────────────────────────────────────────────────────────────────────────────┘
```

完整版（含 mermaid 渲染与两条数据流时序）见 [`docs/architecture.md`](docs/architecture.md)。

## 三、能验收的能力

每一行都对应一个可动手验证的动作。

| 能力 | 验收动作 | 关键实现 |
|---|---|---|
| **多租户知识库隔离** | 建两个库各传一份文档，交叉提问不会串味 | 向量库按库物理隔离（`kb_{id}` 集合） |
| **文档摄取** | 传 PDF / DOCX / MD / TXT，看状态从 pending → ready | `SUPPORTED_EXTS` 注册表 + 解析器分派 |
| **三档切片策略** | 同一份文档用 `fixed` / `structure` / `semantic` 各切一次，对比块边界 | 结构优先（标题/编号条款作块起点）零成本；语义档叠加句级 embedding 找断点 |
| **混合检索** | 搜一个**精确型号/代码**（如 `ERR-4032`），切 `vector` 与 `hybrid` 看召回差异 | 中文 2-gram + 英文保序 tokenize，双路归一化加权融合 |
| **重排 Rerank** | 质检台里开关重排，对比检索分数与重排分数的排序变化 | `RerankProvider` 接口 + Noop / Mock / OpenAICompat 三实现 |
| **流式问答 + 溯源** | 提问看答案逐字吐出，每条引用可展开原文切片 | SSE 流式 + 引用元数据随流下发 |
| **多轮对话** | 先问"报销标准是多少"，再问"那出差呢"——指代能接上 | `QueryRewriter` 规则档离线可用；配真模型自动升级 LLM 改写 |
| **会话持久化** | 刷新页面，历史会话与消息仍在侧边栏 | `Conversation` / `ChatMessage` 表，服务端改写读库内历史 |
| **RBAC 权限** | 用 viewer 账号登录，写操作按钮置灰、后端同步拒绝 | `User.role` + `KnowledgeBaseMember`，owner/editor/viewer + admin 旁路 |
| **可观测四件套** | 质检台 / 摄取诊断 / 问答日志 / 文档健康逐页打开 | 向量一致性检查、死文档识别、命中热力图 |

## 四、3 分钟演示路径

```bash
# ① 零配置冒烟：不联网、不要任何 API Key，看完整链路跑通
python scripts/demo.py

# ② 起服务（两个终端）
python -m uvicorn app.main:app --reload          # 后端 http://127.0.0.1:8000
cd frontend && npm install && npm run dev        # 前端 http://127.0.0.1:5173
```

然后在浏览器里：**注册 → 建知识库 → 传一份 PDF → 提问 → 展开引用看原文**。
接着打开侧边栏的**质检台**，把重排开关拨一下，看同一个问题下排序如何变化——这是最能说明"检索不是黑盒"的一步。

想接真模型，复制 `.env.example` 为 `.env`，填 `DASHSCOPE_API_KEY` 或 `ZHIPU_API_KEY` 即可，代码零改动。

## 五、三个值得展开的技术点

**1. 混合检索为什么必须做。** 纯向量检索在"精确词"上天然吃亏：员工搜工单号 `ERR-4032`、搜制度编号、搜产品型号，语义相似度帮不上忙，反而容易被语义相近但编号不同的内容挤掉。做法是中文切 2-gram、英文保序切词，向量与关键词两路各自归一化后加权融合。踩过的坑：ChromaDB 的 `$contains` **大小写敏感**，同一个编号的大小写变体召回结果不同——写进了 [`docs/bugfix-log.md`](docs/bugfix-log.md)。

**2. 语义切分要分两档，因为成本差一个量级。** `structure` 档只看文档结构（标题层级、编号条款）决定块边界，纯离线、零 token 成本，对制度/手册类文档效果已经很好；`semantic` 档在此基础上用句级 embedding 找语义断点，适合结构松散的会议纪要。做成可切换而不是只做贵的那个，是因为内网环境下 embedding 调用也是成本。

**3. 可降级设计是架构约束，不是补丁。** LLM / Embedding / Rerank 三套供应商各自抽象成接口并有 mock 实现，于是 **187 个测试用例全程不联网、不需要密钥**，`demo.py` 在任何一台干净机器上都能跑通。这条约束反过来逼出了更好的代码结构——供应商层与业务逻辑彻底解耦。

## 六、快速开始

```bash
# 1. 建虚拟环境并装依赖
python -m venv .venv
.venv\Scripts\activate                 # Windows
pip install -e ".[dev]"                # 后端 + 测试依赖
pip install -e ".[api]"                # 需要起 API 服务时

# 2. 配置（可跳过；不配置则用 mock 供应商离线运行）
copy .env.example .env                 # Linux/macOS: cp .env.example .env
#    在 .env 中填 DASHSCOPE_API_KEY 或 ZHIPU_API_KEY

# 3. 冒烟
python scripts/demo.py
```

## 七、项目结构

```
app/
├── main.py              # FastAPI 入口，lifespan，全局异常处理
├── config.py            # pydantic-settings 读 .env
├── core/models.py       # User / KnowledgeBase / KnowledgeBaseMember /
│                        # Document / Conversation / ChatMessage / QaLog
├── storage/
│   ├── db.py            # init_db、get_db 会话
│   └── vector_store.py  # VectorStore 抽象（chroma / pgvector 双实现）
├── api/                 # auth / kbs / documents / chat /
│                        # inspect / diagnostics / qa-logs / doc-health
├── ingestion/           # parsers 解析、chunker 切片、pipeline 编排
├── providers/           # LLM / Embedding / Rerank 三套供应商抽象
└── rag/                 # retriever → reranker → query_rewriter → generator
frontend/src/            # Vue3 前端，10 个页面
docs/                    # 架构图 / 24 张需求卡片 / 进度日志 / 踩坑日志 / 路线图
tests/                   # 187 个离线单测（mock 供应商，无需网络）
```

## 八、核心配置

见 [`.env.example`](.env.example)。最常动的几项：

| 变量 | 取值 | 说明 |
|---|---|---|
| `RAG_PROVIDER` | `mock` / `dashscope` / `zhipu` | `mock` 完全离线，用于开发与测试 |
| `EMBEDDING_MODEL` | 默认 `text-embedding-v3` | 换供应商时注意同步 `EMBEDDING_DIM` |
| `RETRIEVAL_MODE` | `vector` / `hybrid` | 精确词/型号召回差就切 `hybrid` |
| `CHUNK_STRATEGY` | `fixed` / `structure` / `semantic` | 制度手册类优先 `structure` |
| `RERANK` | `true` / `false` | 打开后叠加重排，精度换一点延迟 |
| `VECTOR_STORE` | `chroma` / `pgvector` | 数据量大或需与业务库同库时切 pgvector |

## 九、测试

```bash
python -m pytest tests/ -v --tb=short     # 187 个用例，全程离线
```

测试 fixture 钉死 ChromaDB，不需要 PostgreSQL 就能全量跑；pgvector 相关用例单独标记，连不上时自动跳过。

## 十、进度与路线图

**V1 基础功能与主要进阶功能均已落地**（路线图里 50 个积木全部 ✅，无进行中/待办项）：

- 核心引擎：解析 / 三档切片 / 向量化 / 检索 / 重排 / 生成
- 存储：SQLite + ChromaDB 默认，PostgreSQL + pgvector 可切换
- 后端：认证、RBAC、知识库、文档、流式问答、四个观测面板
- 前端：10 个页面覆盖全流程

**尚未排期**（见 [`docs/roadmap.md`](docs/roadmap.md) 末尾）：异步摄取队列、向量库可插拔、多模型路由配置、点赞/反馈日志、数据看板、管理后台与审计日志、SSO/LDAP、更多格式（Excel / PPT / OCR）、RPA 集成。

## 十一、项目文档

- [系统架构](docs/architecture.md) —— 分层图 + 两条数据流
- [开发路线图](docs/roadmap.md) —— 全部积木与完成状态
- [进度日志](docs/progress-log.md) —— 每个积木的实现记录
- [踩坑日志](docs/bugfix-log.md) —— 27 条按严重度分级的真实问题与修法
- [需求卡片](docs/requirements/) —— 24 张，动工前先写、完成后验收
- [AGENTS.md](AGENTS.md) —— 给 AI 编码助手看的项目上下文
