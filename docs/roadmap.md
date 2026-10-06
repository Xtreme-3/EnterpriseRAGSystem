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
| 四 | 管理后台（运营中心） | 6 | 6 ✅ |
| 五 | 内容质量与性能（K 系列） | 11 | 10 ✅ / 1 ⬜ |
| **合计** | | **47** | **46 ✅ / 1 ⬜** |

> **你现在位置**：功能面 35 块全绿；K0/K1/K2/K3 已完成，**K8（可观测性）与 K9（前端测试框架）**也已补齐 ——
> K9 落地当天就照出一个从未生效的参数（bugfix-log #40）。
> **K3 批 2 收口（2026-09-26）**：引用校验（`citation_issues`）、拒答三级分级、
> 命中阈值 0.1 → 0.40（库外噪声每问 5 条 → 1 条，库内一条不丢）。
> **K5 / K7 完成（2026-10-03）**：K7 对话页参数可视化（top_k / 重排开关 / 模型切换）+
> `GET /api/config/chat`；K5 答案反馈闭环（有用/没用 + 原因标签，落库关联 ChatMessage）。
> **K6 完成（2026-10-05）**：问答缓存两级命中，语义阈值实测定 0.78，同义改写 10.8s → 0.3s。
> **K 系列只剩 K4（暂缓，见下方排期决策）。**

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
| **G6** | **数据看板 `GET /api/kbs/{id}/dashboard` + 前端 `/kbs/:kbId/dashboard`**（G4 日志 + K5 反馈 + K6 命中聚合成一页：概览统计卡 / 常问问题 TOP / 「没用」原因分布 / 死文档；入口在 KbsList 下拉） | ✅ |
| **H1** | **混合检索**（tokenize 中文2-gram+英文保序 + fuse_hybrid 加权归一化；chroma/pgvector search_lexical；`RETRIEVAL_MODE` 配置 + Retriever mode + inspect 返回 mode） | ✅ |
| **H2** | **语义切分**（`split_structure` 结构优先：标题/编号条款为块起点，离线免费；`split_semantic` 结构+句级 embedding 微调；`CHUNK_STRATEGY` 配置 + ingest 接入） | ✅ |
| **I1** | **重排序 Rerank**（`RerankProvider` 接口 + Noop/Mock/OpenAICompat(gte-rerank) 三实现；`RERANK` 配置；质检台重排开关 + 检索/重排前后分数对比） | ✅ |
| **I2** | **RBAC 权限**（`User.role` + `KnowledgeBaseMember` 表；角色 owner/editor/viewer + admin 全局旁路；写接口按角色收紧；成员管理 API + 前端角色徽章/按钮门控/成员对话框） | ✅ |
| **J1** | **多轮对话**（`QueryRewriter` 规则/LLM 双策略：短追问/指代拼最近问句，mock 离线可测，配真模型自动升级 LLM 改写；`AskRequest.history` + 生成 prompt【对话历史】块；前端携带最近 6 轮） | ✅ |
| **J2** | **会话持久化**（`Conversation`/`ChatMessage` 表 + 会话 CRUD；`ask/ask_stream` 带 `conversation_id` → 服务端从库内历史改写 + 落库本轮消息；标题首轮取提问前 30 字；前端会话侧边栏列表/新对话/删除 + 首次发送自动建会话；无 conversation_id 保持 J1 向后兼容） | ✅ |

## 阶段五：内容质量与性能 🔨（K 系列，进行中）

> 排期依据：真实用户视角的第一印象。G–J 让系统"功能齐了"，
> 但**答案读起来不像专业助手写的**、**每次提问都要等**——这两个问题不解决，
> 前面 35 块积木的完成度体现不出来。
>
> **K1 已完成（2026-09-26）**：`/ask/stream` 改为真流式，事件序列
> `stage(改写/检索/重排) → sources → token* → done`；来源前置（引用早于答案可见）、
> 落库后置（收到 done 才写，避免空答案）、错误事件化（流内 `error` 事件，HTTP 仍 200）。
> 桩模型实测：首字节 1.54s → 53ms。
>
> **K2 已完成（2026-09-26）**：`RagPipeline` / `IngestionPipeline` 及全部 provider
> 与向量库在 lifespan 里**装配一次**，请求期取 `app.state.rag` / `app.state.ingestion`
> （摄取与问答共用同一份向量库与 embedding）。实测**每请求固定开销 653ms → 0**
> （10 次请求省 5.9s），根因是 httpx 每个 client 要建 3 个 SSLContext。
> 顺带给 PG engine 补 `pool_pre_ping`、给 OpenAI client 补显式 timeout / max_retries。
>
> **K0 已完成（2026-09-26）**：embedding 从 mock 切到**阿里云百炼官方**
> `qwen3.7-text-embedding`（1024 维真实语义向量），LLM 仍走中转站 —— 三个模型插槽彻底解耦。
> 顺带修掉 `build_reranker` 跟随 LLM 槽位的缺陷（会打到没有 rerank 接口的中转站）、
> 补上 embedding 返回维度自检、补 `tests/conftest.py` 切断单测对开发者本机 `.env` 的依赖。
> 它是 K3 的**前置**：mock 向量没有语义，K3 的 prompt 改动做完了也验证不出好坏。
>
> **K3 批 1 已完成（2026-09-26）**：答案从「资料显示…[1]」变成
> 「根据《供应商管理制度》4.2 节…」。system 规则走独立 `system` 消息、
> 文档名解析提前到**生成之前**（每块渲染来源头「[1]（来源：xxx.pdf · 第 3 块）」）、
> 生成参数（temperature / max_tokens / enable_thinking）配置化。
> 13 问机器评测：**答案点名文档 0/10 → 10/10**，检索命中@1 保持 9/10（未动检索），
> 库外 3/3 正确拒答，平均耗时 7.7s → **4.1s**。
> 顺带照出并修掉三个既有缺陷：**推理 token 挤空正文**（第 9 问实测空答案）、
> 空答案直达前端、prompt 只要求标编号所以模型从不写文档名。
> 详见 [`docs/eval/README.md`](eval/README.md)。
>
> **K3 批 2 已完成（2026-09-26）**，三件事各自独立验收：
> ①**引用编号校验** —— 新增 `citation_issues(answer, source_count)`，贯通 dataclass → API → SSE `done` 事件，
> 越界时记 WARNING（此前模型写出 `[9]` 而只有 5 块时服务端完全无痕，前端是个点不开的假引用）；
> ②**拒答三级分级** —— 从一刀切改为「完全无关 / 只覆盖一部分 / 相互冲突」，
> 真模型探针三场景全部落到设计分支（冲突时并列双方而非擅自选边）；
> ③**命中阈值 0.1 → 0.40** —— 先量后改：库内 rank-5 raw 余弦最低 **0.469**、库外最高 **0.371**，
> 0.40 落在间隙内不贴边。A/B 实测**库内来源一条不丢、库外噪声 15 条 → 1 条**。
> （2026-09-27 语料改写后复测：0.375 / **0.512**，间隙变宽，阈值不变。）
> 详见 [`docs/eval/k3b2-before.md`](eval/k3b2-before.md) / [`k3b2-after.md`](eval/k3b2-after.md)。
>
> **2026-10-03 排期决策**（定位 = 作品集门面，节奏 = 每周零星几小时）：
> 剩余工作走 **K7 → K5 → 封版（v1.0 tag + README 演示图 + 仓库文案）**。
> **K4 暂缓**，触发条件 = 真实出现「大文件摄取慢到不可接受」（100 页 PDF ≈ 25–40 次 embed
> 往返）或面试前想补「异步任务队列」亮点 —— 验收标准已备好，随时可开卡。
> **K6 移入后续候选**（理由见该节）。

> **2026-09-28 多库复测**：新增 kb_2（人事 / 财务 / 税务，3 份 / 75 切片）后，
> kb_1 仍是 0.3750 / 0.5119（阈值有效），但 **kb_2 两条分布重叠** ——
> 噪声上限 0.4726 **高于**库内最弱 0.4652。原因不是检索排序差，而是那两条「库外问题」
> （五金件电镀厚度、美国站退货窗口）问的是 kb_1 的业务制度，与人事财务**同属一个语义域**。
> **不改阈值**：调高会连带筛掉库内最弱那问；实测噪声虽进了 prompt，LLM 仍正确拒答（3/3）。
> 定位因此是「召回优先的粗筛」，**拒答由模型把守、不是由阈值把守**。
> rerank 实测也救不了（指标一项没变、延迟 +21%）。详见 [`docs/eval/README.md`](eval/README.md)。

| 块 | 功能 | 需求卡片 | 状态 |
|---|---|---|---|
| **K0** | **真实 Embedding 接入（K3 前置）**：新增 `bailian` 供应商槽位（阿里云百炼官方，与 LLM 走的中转站彻底解耦）+ `rerank_provider` 独立槽位 + 百炼原生 rerank 实现（compatible 层没有 rerank）+ embedding 返回维度自检 + `tests/conftest.py` 切断单测对 `.env` 的依赖 | [K0](requirements/K0-real-embedding.md) | ✅ |
| **K1** | **真流式生成**：`LLMProvider.stream()` + `RagPipeline.ask_stream()` 事件化 + `/ask/stream` 去掉"先同步生成再假打字机" + 前端阶段文案 | [K1](requirements/K1-streaming-generation.md) | ✅ |
| **K2** | **管线单例与连接复用**：lifespan 装配 `app.state.rag` / `app.state.ingestion`，砍掉每请求重建（httpx 客户端构造 + PG 连接池）| [K2](requirements/K2-pipeline-singleton.md) | ✅ |
| **K3** | **答案质量**：Prompt 重构（system/user 分离 + 上下文带文档名/块号）+ 生成参数配置化 + 推理 token 截断防护 + **批 2：引用编号校验 `citation_issues` / 拒答三级分级 / 命中阈值 0.1→0.40** | [K3](requirements/K3-answer-quality.md) | ✅ |
| **K4** | **摄取异步化**：上传即返回 + `IngestionJob` 进度表 + 后台线程池 + 重试接口 + 前端进度条 | [K4](requirements/K4-async-ingestion.md) | ⬜ |
| **K5** | **答案反馈闭环**：对话页赞/踩 + 可选原因标签 + 落库关联 `ChatMessage`（J2 已预留挂载点）| [K5](requirements/K5-answer-feedback.md) | ✅ |
| **K6** | **问答缓存**：精确命中（键全等）→ 语义命中（余弦 ≥ 阈值，**实测定 0.78**）| [K6](requirements/K6-qa-cache.md) | ✅ |
| **K7** | **对话页参数可视化**：top_k / 检索模式 / Rerank 开关 / **模型切换**（`LLM_MODEL_OPTIONS` 清单内快慢自选）| [K7](requirements/K7-chat-params.md) | ✅ |
| **K8** | **可观测性与错误提示**：`LOG_LEVEL` 可配 + 配置 root 收编第三方 logger + `X-Request-ID` 贯穿（响应头/日志/500 响应体）+ 12 处静默失败点开口 + 前端错误归一化（422 数组不再渲染成 `[object Object]`）+ 伪永久加载/静默降级/abort 误报修复 | [K8](requirements/K8-observability.md) | ✅ |
| **K9** | **前端测试框架**：引入 vitest + jsdom + @vue/test-utils，51 用例覆盖纯函数 / store / 拦截器 / 路由守卫 / 组件挂载；补齐 K8 留下的「前端改动无法走 TDD」缺口 | [K9](requirements/K9-frontend-testing.md) | ✅ |
| **K10** | **Excel 格式支持**：openpyxl 解析器（sheet → `## 小节`、行转「列值 \| 列值」），`SUPPORTED_EXTS` 注册 `.xlsx`；受支持扩展名改由 `GET /api/config/upload` 后端下发（消灭前端三处硬编码漂移源） | [K10](requirements/K10-excel-support.md) | ✅ |

### 为什么是这个顺序

1. **K1 必须最先**。假流式让每次验证都要盲等 8 秒，不先修，后面所有改动都没法高效迭代。
2. **K2 紧跟（已完成）**。改动最小（主要是 lifespan 装配），却直接砍掉每请求约 653ms
   的固定开销（实测），与 K1 叠加效果最明显。
3. **K0 先于 K3（已完成）**。K3 治的是"内容不符合要求"，前提是能连真 Key 实测；
   但更底层的前提是**检索侧得是真的**——embedding 此前一直是 mock（64 维哈希词袋，
   无语义），检索召回质量本身不可评测，K3 改完了也看不出好坏。K0 把 embedding 换成
   百炼官方真实语义向量，K3 才有可验证的基础。
4. **K4 第四**。影响的是上传体验，与问答体验独立，可以在 K3 之后从容做。
5. **K8 插入在 K4 之前（2026-09-26）**。它不是新功能，而是**给已有功能装仪表盘**：
   一次只读审计发现日志只覆盖 6/34 个模块、21 个有代码的模块零日志、后端 12 处
   前端 8 处静默失败。其中最典型的三条 —— 非流式问答失败**一行日志都没有**、
   摄取收尾失败会让文档**永久卡在 `processing`**、422 校验错误在前端渲染成
   `[object Object]` —— 都是"用户能碰到、但服务端查不出来"的类型。
   不先补这一层，K4 之后做的任何异步化都会变成新的黑盒。详见
   [K8 需求卡片](requirements/K8-observability.md) 与
   [bugfix-log 第 28–39 条](bugfix-log.md)。
6. **K9 紧跟 K8（2026-09-26）**。K8 收尾时明确留下一条遗留：前端没有测试运行器，
   所以 K8 那批前端改动（20 处错误解析传参、8 个页面的错误渲染、`Chat.vue` 的 abort 分流）
   **无法走 TDD**，只能靠类型检查 + 构建 + 手工复现。
   代价在 K9 落地的**当天**就兑现了：新写的第一个断言直接照出一个从未生效的参数
   （[bugfix-log #40](bugfix-log.md) —— `extractErrorMessage` 的 `fallback` 被 20 处传参、
   文档描述、却没有任何代码路径会读它）。**这一条是先有测试、后发现的缺陷，不是反过来。**
   详见 [K9 需求卡片](requirements/K9-frontend-testing.md)。

## 后续候选（暂不拆块）

按 README「迭代路线图」保留：管理后台（用户管理/系统配置/审计日志查询）、
文档增量更新、向量库可插拔、多模型配置、审计日志、SSO/LDAP、
更多格式（PPT/OCR；**Excel 已完成 → K10**，2026-10-06）、
RPA 集成（Playwright 浏览器自动化 + RAG 智能决策）。
已拆卡：点赞/反馈日志 → K5、异步摄取队列 → K4（暂缓）、向量库可插拔与多模型 → K7、
数据看板 → G6（2026-10-06 完成）、Excel 格式 → K10（2026-10-06 完成）。

**移入（2026-10-03）→ 已完成移出（2026-10-05）**：**K6 问答缓存** —— 当时的顾虑是
语义缓存的串库/错答风险；开工后以「实测阈值 + 评测护栏」化解：同义对最低 0.8092 /
易混对最高 0.7559 的间隙内取 0.78，`scripts/k6_cache_eval.py` 钉住同义要命中、
易混与跨库串扰不许命中。

**新增候选（2026-09-28，来自 kb_2 的阈值实测）** —— **拒答不再完全依赖 LLM 自觉**：

kb_2 暴露出「两次分布重叠」时，当前唯一挡住噪声的是模型自己说「未找到相关信息」。
这在换更弱的 LLM（如 turbo 级）时是**可能失守的**——它会开始拿不相关上下文硬编答案。
候选做法：**双阈值** —— `recall_threshold`（粗筛，现 0.40）+ `answer_threshold`（强相关线），
当所有候选都低于 `answer_threshold` 且关键词路 0 命中时，直接返回标准拒答、
不必再调 LLM（顺带省一次调用）。等有真实失败案例再拆卡，不凭空做。
