# 项目总地图（积木路线图）

> **一个积木 = 一次动手**：动工前写需求卡片 → 实现 → 验证 → 更新进度日志。
> 阶段只是松散分组，**真正的最小单位是积木**。每一块做不完不进入下一块。

## 图例

- ✅ 已完成　🔨 进行中　⬜ 待办　❌ 失败/回退
- `→` 表示依赖（须先做前置）

## 当前进度一览

| 阶段 | 内容 | 块数 | 完成 |
|---|---|---|---|
| 一 | 核心引擎（发动机） | 9 | 9 ✅ |
| 二 | 存储切换 + 后端 API（仪表盘） | 16 | 16 ✅ |
| 三 | 前端 Vue3（外壳） | 5 | 5 ✅ |
| 四 | 管理后台（运营中心） | 5 | 5 ✅ |
| 五 | 内容质量与性能（K 系列） | 7 | 4 已开卡 ⬜ |
| **合计** | | **42** | **35 ✅ / 7 ⬜** |

> **你现在位置**：功能面 35 块全绿，但"能跑"≠"好用"。
> 真实用户会立刻撞上两件事 —— **答案不像人说的**（prompt + 没配 Key）和
> **一问就卡好几秒**（假流式 + 每请求重建管线）。阶段五专治这两件事，见下方 K 系列。

---

## 阶段一：核心引擎 ✅（已完成）

| 块 | 功能 | 状态 |
|---|---|---|
| A1 | 项目骨架（venv/pyproject/gitignore/env/README） | ✅ |
| A2 | 配置模块 `app/config.py` | ✅ |
| A3 | 模型供应商抽象 + mock + 通义/智谱实现 | ✅ |
| A4 | SQLite 元数据模型与存储 | ✅ |
| A5 | ChromaDB 向量库（接口抽象） | ✅ |
| A6 | 文档解析（PDF/TXT/MD） | ✅ |
| A7 | 递归切片 chunker | ✅ |
| A8 | 摄取流水线（解析→切片→向量化→入库） | ✅ |
| A9 | RAG 编排（检索→重排→生成→溯源） | ✅ |

## 阶段二：存储切换 + 后端 API ✅（已完成）

| 块 | 功能 | 依赖 | 状态 |
|---|---|---|---|
| **A10** | **存储层切换 PostgreSQL + pgvector**（元数据+向量一套库） | A1–A9 | ✅ |
| **B1** | **FastAPI 骨架 + `/health` 健康检查** | A10 | ✅ |
| B2-1 | 用户注册接口 `POST /api/auth/register` | B1 | ✅ |
| B2-2 | 用户登录 + JWT 签发 `POST /api/auth/login` | B2-1 | ✅ |
| B2-3 | 鉴权依赖（从 JWT 解析当前用户） | B2-2 | ✅ |
| B2-4 | 获取当前用户 / 登出 | B2-3 | ✅ |
| B3-1 | 创建知识库 `POST /api/kbs` | B2 | ✅ |
| B3-2 | 知识库列表 / 详情 `GET /api/kbs` | B3-1 | ✅ |
| B3-3 | 删除知识库 `DELETE /api/kbs/{id}` | B3-2 | ✅ |
| B4-1 | 文档上传 + 同步向量化 `POST /api/kbs/{id}/documents` | B3 | ✅ |
| B4-2 | 摄取状态查询 `GET /api/documents/{id}` | B4-1 | ✅ |
| B5-1 | 文档列表 `GET /api/kbs/{id}/documents` | B4 | ✅ |
| B5-2 | 文档删除 `DELETE /api/documents/{id}` | B5-1 | ✅ |
| B6-1 | 非流式问答接口（先打通全链路） | B5 | ✅ |
| B6-2 | SSE 流式问答 | B6-1 | ✅ |
| B6-3 | 溯源来源结构化返回（文档名/切片/相似度） | B6-2 | ✅ |

## 阶段三：前端 Vue3 ✅（已完成）

| 块 | 功能 | 依赖 | 状态 |
|---|---|---|---|
| F1 | 项目脚手架 + 登录/注册页 | 阶段二全部 | ✅ |
| F2 | 知识库管理页（列表/创建/删除） | F1 | ✅ |
| F3 | 文档上传 / 管理页（列表/删除/状态） | F2 | ✅ |
| F4 | 对话页面（流式渲染 + 引用来源展示） | F3 | ✅ |
| F5 | 整体联调、错误处理、样式打磨 | F4 | ✅ |

## 进阶功能（V1 之后，动工前再拆块）

| 块 | 功能 | 状态 |
|---|---|---|
| **G1** | **检索质检台 `POST /api/kbs/{id}/inspect` + 前端 `/kbs/:kbId/inspect`** | ✅ |
| **G2** | **摄取诊断面板 `GET /api/kbs/{id}/diagnostics` + 前端 `/kbs/:kbId/diagnostics`** | ✅ |
| **G3** | **向量一致性检查（VectorStore.document_counts + consistency 区块）** | ✅ |
| **G4** | **问答日志 `GET /api/kbs/{id}/qa-logs` + 埋点（ask/ask_stream）+ 前端 `/kbs/:kbId/qa-logs`** | ✅ |
| **G5** | **文档健康分析 `GET /api/kbs/{id}/doc-health` + 前端 `/kbs/:kbId/doc-health`**（死文档 + 命中热力，复用 G4 日志） | ✅ |
| **H1** | **混合检索**（tokenize 中文2-gram+英文保序 + fuse_hybrid 加权归一化；chroma/pgvector search_lexical；`RETRIEVAL_MODE` 配置 + Retriever mode + inspect 返回 mode） | ✅ |
| **H2** | **语义切分**（`split_structure` 结构优先：标题/编号条款为块起点，离线免费；`split_semantic` 结构+句级 embedding 微调；`CHUNK_STRATEGY` 配置 + ingest 接入） | ✅ |
| **I1** | **重排序 Rerank**（`RerankProvider` 接口 + Noop/Mock/OpenAICompat(gte-rerank) 三实现；`RERANK` 配置；质检台重排开关 + 检索/重排前后分数对比） | ✅ |
| **I2** | **RBAC 权限**（`User.role` + `KnowledgeBaseMember` 表；角色 owner/editor/viewer + admin 全局旁路；写接口按角色收紧；成员管理 API + 前端角色徽章/按钮门控/成员对话框） | ✅ |
| **J1** | **多轮对话**（`QueryRewriter` 规则/LLM 双策略：短追问/指代拼最近问句，mock 离线可测，配真模型自动升级 LLM 改写；`AskRequest.history` + 生成 prompt【对话历史】块；前端携带最近 6 轮） | ✅ |
| **J2** | **会话持久化**（`Conversation`/`ChatMessage` 表 + 会话 CRUD；`ask/ask_stream` 带 `conversation_id` → 服务端从库内历史改写 + 落库本轮消息；标题首轮取提问前 30 字；前端会话侧边栏列表/新对话/删除 + 首次发送自动建会话；无 conversation_id 保持 J1 向后兼容） | ✅ |

## 阶段五：内容质量与性能 ⬜（K 系列，进行中）

> 排期依据：真实用户视角的第一印象。G–J 让系统"功能齐了"，
> 但**答案读起来不像专业助手写的**、**每次提问都要等**——这两个问题不解决，
> 前面 35 块积木的完成度体现不出来。

| 块 | 功能 | 需求卡片 | 状态 |
|---|---|---|---|
| **K1** | **真流式生成**：`LLMProvider.stream()` + `RagPipeline.ask_stream()` 事件化 + `/ask/stream` 去掉"先同步生成再假打字机" + 前端阶段文案 | [K1](requirements/K1-streaming-generation.md) | ⬜ |
| **K2** | **管线单例与连接复用**：lifespan 装配 `app.state.rag`，砍掉每请求重建（TLS 握手 + PG 连接池）| [K2](requirements/K2-pipeline-singleton.md) | ⬜ |
| **K3** | **答案质量**：Prompt 重构（system/user 分离 + 分点结构 + 拒答分级）+ 上下文带文档名/块号/章节 + 引用编号校验 | [K3](requirements/K3-answer-quality.md) | ⬜ |
| **K4** | **摄取异步化**：上传即返回 + `IngestionJob` 进度表 + 后台线程池 + 重试接口 + 前端进度条 | [K4](requirements/K4-async-ingestion.md) | ⬜ |
| **K5** | **答案反馈闭环**：对话页赞/踩 + 可选原因标签 + 落库关联 `ChatMessage`（J2 已预留挂载点）| 待开卡 | ⬜ |
| **K6** | **问答缓存**：精确命中（KB+query+mode+top_k）→ 语义命中（embedding 余弦 > 0.95）| 待开卡 | ⬜ |
| **K7** | **对话页参数可视化**：top_k / 检索模式 / Rerank 开关 / **模型切换**（qwen-plus ↔ qwen-turbo，快慢自选）| 待开卡 | ⬜ |

### 为什么是这个顺序

1. **K1 必须最先**。假流式让每次验证都要盲等 8 秒，不先修，后面所有改动都没法高效迭代。
2. **K2 紧跟**。改动最小（主要是 lifespan 装配），却直接砍掉首字节里一段固定开销，
   与 K1 叠加效果最明显。
3. **K3 第三**。它治的是"内容不符合要求"，但**前提是能连真 Key 实测**——
   在 mock 下改 prompt 无法验证效果。
4. **K4 第四**。影响的是上传体验，与问答体验独立，可以在 K3 之后从容做。

## 后续候选（暂不拆块）

按 README「迭代路线图」保留：数据看板、管理后台（用户管理/系统配置/审计日志查询）、
文档增量更新、向量库可插拔、多模型配置、审计日志、SSO/LDAP、更多格式、
RPA 集成（Playwright 浏览器自动化 + RAG 智能决策）。
已拆入 K 系列：点赞/反馈日志 → K5、异步摄取队列 → K4、向量库可插拔与多模型 → K7。
