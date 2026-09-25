# 进度日志

> 每个功能积木一条。状态：✅ 完成 / 🔨 进行中 / ⬜ 待办 / ❌ 失败 / ↩️ 回退
> 工作方式见 [README.md](README.md) 与 `requirements/TEMPLATE.md`。
> **完整积木地图见 [roadmap.md](roadmap.md)**（本文件只记状态与验证结果）。

## 阶段一：核心引擎（2026-08-06 完成，按积木记录）

| 编号 | 功能积木 | 状态 | 验证结果 |
|---|---|---|---|
| A1 | 项目骨架（venv / pyproject / gitignore / env.example / README） | ✅ | 依赖安装成功，`pytest` 可运行 |
| A2 | 配置模块 `app/config.py` | ✅ | 集成测试覆盖 |
| A3 | 模型供应商抽象 + mock + DashScope/智谱 OpenAI 兼容实现 | ✅ | 单元测试 |
| A4 | SQLite 元数据模型（KB/Document/Chunk）+ 存储 | ✅ | 元数据落库测试 |
| A5 | ChromaDB 向量库（接口抽象） | ✅ | 检索测试 |
| A6 | 文档解析（pdf / docx / md / txt） | ✅ | 单元测试 |
| A7 | 自研递归切片 chunker | ✅ | 6 个单测通过 |
| A8 | 摄取流水线（解析→切片→embed→入库） | ✅ | 全链路测试 |
| A9 | RAG 编排（检索→重排→生成→引用） | ✅ | **12 测试全绿 + `demo.py` 端到端跑通** |
| A10 | 存储层切换 PostgreSQL + pgvector（元数据+向量一套库） | ✅ | 容器 pgvector:pg17 就绪；demo 全链路跑通；修复 3 个 pgvector bug；**13 测试全绿** |

失败 / 回退记录：阶段一无 ❌ / ↩️。

## 阶段二：API 服务（积木式推进，V1 基础功能）

> V1 目标见 [README](../README.md#-v1-目标基础功能)。按顺序一块块搭，动工前先写需求卡片。

| 编号 | 功能积木 | 状态 | 说明 |
|---|---|---|---|
| B1 | FastAPI 应用骨架 + `/health` 健康检查 | ✅ | 起服务、统一响应/异常、日志；uvicorn 实测通过，**16 测试全绿** |
| B2 | 用户登录 + JWT 会话 | ✅ | 注册/登录/鉴权/me/登出；**8 测试全绿** |
| B3 | 多知识库隔离（知识库 CRUD） | ✅ | 创建/列表/详情/删除 + 用户隔离；KnowledgeBase 加 user_id 外键；**34 测试全绿（含新增 10 个 KB 测试）** |
| B4 | 文档上传 + 向量化 API | ✅ | multipart 上传 → 同步向量化；状态查询；10 测试全绿 |
| B5 | 文档管理 API（列表/删除） | ✅ | 列表 + 级联删除；50 测试全绿 |
| B6 | 流式问答 + 溯源 API | ✅ | SSE + 非流式；5 测试全绿 |

> 进阶功能（RBAC/混合检索/Rerank/多轮对话/反馈日志/看板等）见 README「迭代路线图」，V1 基础功能完成后逐块排期。

## 迭代记录

| 日期 | 做了什么 | 结果 |
|---|---|---|
| 2026-08-06 | 阶段一 9 块积木（A1–A9）全部实现 | 12 测试全绿，demo 跑通；后续改为积木式开发 |
| 2026-08-06 | 定义 V1 基础/进阶功能清单，产出架构图（docs/architecture.md），写入 README | 功能分级：登录✅/多库✅/上传✅/问答✅/文档管理✅为基础；RBAC、混合检索、点赞日志、看板等为进阶 |
| 2026-08-06 | 拆出完整积木地图（docs/roadmap.md）：V1=30 块，9 完成 | 定位清晰：下一步 A10 存储切换，再 B1 |
| 2026-08-06 | 决策：主存储切换 **PostgreSQL + pgvector**（方案 A，现在换） | 理由：简历含金量 + 一套库管元数据与向量 + 原生全文检索支撑混合检索；安装方式待定（winget/手动） |
| 2026-08-06 | A10 存储层切换 pgvector（代码实现完成） | PostgreSQL 17 通过 winget 安装成功；config/db/models/vector_store 全链路更新；12 测试全绿；pgvector 扩展 DLL 和 postgres 密码配置待用户手动完成 |
| 2026-08-06 | 决策改为 **Docker 跑 pgvector**（避免 Windows 原生编译）；启动容器、建 .env、装依赖、跑 demo | 首次实跑 pgvector 路径暴露并修复 3 个 bug（register_vector 参数/时序 + list 绑参格式）；测试 fixture 钉死 chroma；新增 pgvector 集成测试；**13 测试全绿**；A10 完成 |
| 2026-08-06 | **B1 FastAPI 骨架 + /health 完成** | `app/main.py`（lifespan 初始化 DB + 全局异常处理器 + 日志 UTF-8）；uvicorn 实测 `/health`/`/`/404/`/docs` 通过；新增 3 个 health 测试；**16 测试全绿** |
| 2026-08-07 | **B2 用户登录 + JWT 会话完成** | `app/api/auth.py` 4 端点（register/login/me/logout）+ `app/api/deps.py` JWT 鉴权依赖 + `app/core/models.py` User 模型；安装 python-jose/passlib/bcrypt 依赖；**24 测试全绿（含新增 8 个鉴权测试）** |
| 2026-08-07 | **B3 知识库 CRUD 完成** | `app/api/kbs.py` 4 端点（create/list/detail/delete）+ 用户隔离（403 跨用户拦截）；KnowledgeBase 模型新增 user_id 外键；修复 pipeline/demo 兼容；**34 测试全绿（含新增 10 个 KB 测试）** |
| 2026-08-07 | **B4 文档上传 + 向量化 API 完成** | `app/api/documents.py` 2 端点（upload/status）+ multipart 上传 + 同步向量化 + 临时文件清理；pipeline 新增 `display_name` 参数；**44 测试全绿** |
| 2026-08-07 | **B5 文档管理 API 完成** | 列表 + 级联删除（向量 + 元数据）；修复 pipeline 重复删除警告；**50 测试全绿（含新增 6 个 B5 测试）**
| 2026-08-07 | **B6 流式问答 + 溯源 API 完成** | 非流式 `POST /api/kbs/{id}/ask` + SSE 流式 `POST /api/kbs/{id}/ask/stream`；token 级推送 + sources 溯源；**55 测试全绿（含新增 5 个 B6 测试）** |
| 2026-08-07 | **F1 前端脚手架 + 登录/注册页完成** | Vue3 + Vite + Element Plus + Pinia + vue-router；Login/Register 页面 + JWT 存储 + 路由守卫；后端 55 测试全绿 |
| 2026-08-07 | **F2 知识库管理页完成** | DefaultLayout 导航栏 + KbsList 卡片网格 + 创建对话框 + 删除二次确认；后端 55 测试全绿 |
| 2026-08-07 | **F3 文档上传与管理页完成** | DocList 表格 + 拖拽上传对话框 + 状态标签 + 删除；DocumentResponse 补 created_at；后端 55 测试全绿 |
| 2026-08-07 | **F4 对话页完成** | Chat.vue SSE 流式解析 + 聊天气泡 + 闪烁光标 + 引用来源折叠面板；路由入口从 KB 卡片和文档页接入；后端 55 测试全绿 |
| 2026-08-07 | **F5 整体联调与打磨完成** | 新增 404 页面；DocList 面包屑显示 KB 名称；Chat 清理未使用导入；路由修复已登录 404；**V1 全部 30 块积木完成 🎉** |
| 2026-08-07 | **G1 检索质检台完成** | `POST /api/kbs/{id}/inspect` 检索诊断接口（只跑 retriever，不含 LLM 生成）+ 前端 `/kbs/:kbId/inspect` 页面（query 输入/top_k 调节/分数条可视化/切片展开）；**63 测试全绿（含新增 8 个 G1 测试）** |
| 2026-08-07 | **G1 代码审计 + 修复** | 审计 4 项：F1 分数条对负相似度越界（钳制 [0,100]）、F2 死参数、F3 绕过 API 层、F4 502 吞日志；全部修复，63 测试保持全绿 |
| 2026-08-07 | **G2 摄取诊断面板完成** | `GET /api/kbs/{id}/diagnostics` 体检接口（状态分布/失败原因聚合/异常清单）+ 前端 `/kbs/:kbId/diagnostics` 页面（统计卡片/失败分组/异常表格）+ KB 卡片补「质检」「体检」入口；**68 测试全绿（含新增 5 个 G2 测试）** |
| 2026-08-07 | **G3 向量一致性检查完成** | `VectorStore` 接口加 `document_counts`（pgvector SQL GROUP BY / chroma get+聚合）；诊断接口加 `consistency` 区块（missing_index/count_drift/orphan_vector 三类漂移），向量库不可达时 checked=False 兜底 200；前端体检页加一致性区块；**73 测试全绿（含新增 5 个 G3 测试：pgvector 计数、chroma smoke、3 个一致性场景）** |
| 2026-08-07 | **G4 问答日志埋点完成** | `QaLog` 模型（query/answer/hit_doc_ids JSON/hit_count）+ `ask`/`ask_stream` 成功路径埋点（写日志异常吞掉，不 500）+ `GET /api/kbs/{id}/qa-logs` 历史查询（时间倒序、limit 1–100、空命中 hit_doc_ids=[]）+ 前端问答历史页（answer 可展开）；修复 FK 回归（QaLog 对 users/KB 加 ON DELETE CASCADE，重建 PG 中 `qa_logs` 表）；**79 测试全绿（含新增 6 个 G4 测试）** |
| 2026-08-07 | **G5 文档健康分析完成** | `GET /api/kbs/{id}/doc-health` 聚合 G4 问答日志 → 死文档清单（从未命中，带 status/chunk_count/error）+ 命中热力 top N（hit_count 降序、last_hit_at）+ summary（active/dead/hit_rate）；前端 `/kbs/:kbId/doc-health` 页面（统计卡/死文档表/热力条形）；KB 卡片补「健康」入口；**顺带修复检索无相似度阈值 bug**（`SIMILARITY_THRESHOLD=0.1`，Chroma/pgvector 双实现过滤无关噪声，详见 bugfix-log #27）；**86 测试全绿（含新增 7 个 G5 测试）** |
| 2026-08-10 | **H1 混合检索完成** | `tokenize`（中文 2-gram + 英文/数字整词保序）+ `fuse_hybrid` 加权归一化融合（`final = w*vec + (1-w)*lex_norm`）；pgvector `content_tokens` 列 + GIN 全文索引 + 幂等迁移回填；chroma `search_lexical`（Python 侧大小写不敏感子串匹配，修复 `$contains` 大小写敏感导致的型号/代号漏召回）；`RETRIEVAL_MODE` 配置（默认 hybrid）+ `Retriever.retrieve(mode=)` + inspect 接口返回 mode；**修复中断遗留的 chroma 抽象类实例化 bug**（缺失 abstractmethod 实现）；**107 测试全绿（含新增 15 hybrid + 2 pgvector + 4 inspect 测试）** |
| 2026-08-10 | **前端检索模式切换完成（H1 收尾）** | chat 链路透传 mode：`RagPipeline.ask(top_k, mode)` + `AskRequest.mode`（非法值 422）；前端 inspect 质检台 + 对话页加 vector/hybrid 切换（后端 hybrid 默认）；InspectBench 展示实际所用 mode；**109 测试全绿（+2 chat mode 测试）** + `npm run build` 通过 |
| 2026-08-10 | **H2 语义切分完成** | `chunker.split_structure`（结构优先：Markdown # / 第X章·节·条 / 编号条款为新块起点，段落并入直至下个标题，超长节回退递归切分，碎块合并）+ `split_semantic`（结构 + 句级 embedding 余弦断层微调，embed 可注入离线可测）；`Settings.chunk_strategy`（fixed 默认回归 / structure / structure+semantic）+ `semantic_break_threshold`；`ingest_file` 按策略分流；语义切分不做跨块重叠保持主题纯度；**122 测试全绿（+13 语义切分测试）** |
| 2026-08-11 | **I1 重排序完成** | `RerankProvider.rerank(query, texts, scores)` 接口加原始分；三实现：`NoopRerank`（恒等，回归 0 影响）/ `MockRerank`（复用 H1 tokenize 词重叠率打分，离线可测）/ `OpenAICompatRerank`（POST {base_url}/rerank 走 DashScope gte-rerank，results 解析可单测）；`Settings.rerank`（默认 false）+ `rerank_model`；`Reranker.rerank` 用 `dataclasses.replace` 更新命中分数为重排分（排序与分数一致）；inspect 请求加 `rerank` 开关、响应加 `rerank` + 每条 hit `pre_score`（检索/重排前后对比）；前端质检台加重排开关 + 检索(灰)/重排(主色) 双分数条；**140 测试全绿（+14 rerank 单测 + 4 inspect 集成测试）** + `npm run build` 通过 |
| 2026-08-11 | **I2 RBAC 权限完成** | `User.role`（user/admin）+ 新表 `KnowledgeBaseMember`（editor/viewer，`UniqueConstraint(kb_id,user_id)`，owner 不入表=创建者权威）；PG 幂等 `ALTER users ADD COLUMN role`（复用 H1 IF NOT EXISTS 模式）；`get_user_kb_role`（admin→owner 全局旁路 / 创建者→owner / 成员→其角色）+ `_get_user_kb_or_403(required=)` 角色门槛（默认 viewer 现有调用零改动）；写接口收紧：上传/删文档→editor+、删库→owner；`list_kbs` 返回拥有+成员库且每条带 `role`，admin 全部；成员管理 4 端点（GET editor+/POST/PATCH/DELETE owner，404/409/400/422 校验齐全）；`/auth/me` 带 role；前端：auth store 持久化 role + 刷新恢复、头部 admin 徽章、KB 卡片角色徽章（owner 橙/editor 绿/viewer 灰）+ 删除/成员按钮按 role 门控、成员管理对话框（添加/升降级/移除）、DocList 上传/删除按 editor+ 门控 + 只读提示；**153 测试全绿（+13 rbac 集成测试）** + `npm run build` 通过 |
| 2026-08-11 | **J1 多轮对话完成** | `QueryRewriter` 双策略：无历史恒等（单轮零开销）/ 规则改写（短问句≤20字或指代连词 那/这/它/再/具体/为什么 等 → 拼最近用户问句，确定性离线可测）/ LLM 改写（配真模型 `use_llm=True`，改写 prompt 输出独立搜索词，空/异常/Mock 兜底文案时回退规则）；`RagPipeline.ask(kb_id, query, history=)`：**检索用改写词、答案 prompt 用原 query**，返回 `rewritten_query`；`Generator.build_prompt` 加【对话历史】块（在【资料】前，无历史时模板与单轮字节一致）；`AskRequest.history: list[ChatTurn]`（上限 20，非法 role 422）；`ask_stream` sources 事件带 rewritten_query；**顺带加固 MockLLM `_parse_prompt` 只截【资料】→【用户问题】区域**（历史含 "[数字]" 不污染块解析）；前端 Chat.vue 发送携带最近 6 轮 history（排除流式消息/来源）；**174 测试全绿（+21 multiturn 测试）** + `npm run build` 通过 |
| 2026-08-11 | **J2 会话持久化完成** | `Conversation`（kb/user FK CASCADE、title 默认"新对话"、updated_at）+ `ChatMessage`（role/content/sources JSON/rewritten_query）两表（create_all 建表，无需 ALTER）；会话 CRUD 5 端点 `/api/kbs/{id}/conversations`（POST 201/列表 updated_at 倒序/详情含消息 id 正序/PATCH 改名/DELETE 级联删消息），归属校验（他人 403 / 别库 404 / 未登录 401），viewer+ 可访问、会话用户私有；`AskRequest.conversation_id: int|None` → **服务端从库内历史推导**（前端不再传 history）+ 生成成功后落库本轮 user/assistant 消息（assistant 带 sources/rewritten_query）+ `_touch_conversation`（updated_at、首条设 title=提问前 30 字），失败 502 不落半条；`ask_stream` sources 事件带 conversation_id；前端 Chat.vue 会话侧边栏（列表/新对话/删除）+ 首次发送自动建会话 + 选中会话 GET 详情加载 + 切换中断在途流式；无 conversation_id 完全向后兼容 J1；**187 测试全绿（+13 conversation 测试）** + `npm run build` 通过 |
| 2026-09-25 | **AI 助手上下文与架构文档整备（非功能块）** | 新增 `AGENTS.md`（技术栈/目录/开发约定/API 约定/数据库/测试/环境/状态表），替代原 68 行英文且内容停在 B3 的旧文档；`CLAUDE.md` 瘦身为 5 行指路文件，消除与 AGENTS.md 双份不同步；`docs/architecture.md` 按 `east_asian_width` 显示宽度重建 ASCII 分层图（5 框统一 78 宽，去掉内容行无对应竖线的 ┬/┴），导语与 mermaid 去掉已过期的「V1 只做基础功能」与 `:::future` 标记 |
| 2026-09-25 | **README 重写为作品说明版 + 容器化交付** | README 从「给自己看的施工单」改为「为什么做 → 架构 → 能验收什么 → 怎么演示 → 技术难点」结构，每项能力配可动手的验收动作；新增 `Dockerfile`（API，依赖从 pyproject 抽取以复用缓存）、`frontend/Dockerfile`（node 构建 → nginx）、`frontend/nginx.conf`（SPA history 回落 + `/api` 反代 + **SSE 关缓冲**）、`docker-compose.yml`（`web` + `api` + `db(pgvector/pgvector:pg17)`，默认 pgvector 共库 + mock 供应商，无 Key 可跑）+ 两个 `.dockerignore`；`docker compose config` 校验通过、`npm run build` 通过；镜像构建待本机 Docker Desktop 启动后验证 |
| 2026-09-26 | **接入真实 LLM 实测（中转站 qwen3.8-flash）** | 中转站 tokenrhythm 只有对话模型、**没有任何 embedding 模型**（`/models` 23 个模型中 embedding/rerank 类为空，7 个候选名全部 400）。为让"LLM 真实 / embedding 离线"可组合，给三插槽加了**插槽级供应商覆盖**：`Settings.embedding_provider` / `llm_provider`（留空跟随 `rag_provider`），`factory._resolve_endpoint` 参数化供应商名 —— 这是路线图「多模型配置」的第一小块。`.env`：`RAG_PROVIDER=dashscope`（槽位名，实际指向中转站）+ `DASHSCOPE_BASE_URL=https://tokenrhythm.studio/v1` + `LLM_MODEL=qwen3.8-flash` + `EMBEDDING_PROVIDER=mock`（现有 chroma dim=64 数据继续有效，无需重建）。**74 passed / 113 skipped**（skipped 全部为 PostgreSQL 不可达的既有条件跳过，非本次回归）。实测 3 问（hybrid 模式，demo 账号 · 演示知识库 · 供应商管理制度.pdf 15 切片）：①「供应商准入需要满足哪些条件？」**16.7s**，答案高度结构化（总起 + 基本资质 6 条 + 评审流程 5 条 + 适用范围），每条带 [1][2][4] 引用，数字与原文一致（注册资金≥100 万、验厂 70 分淘汰、试产 300-500 件合格率≥98%）——**现有简陋 prompt + 强模型已经接近 K3 想要的效果**；②③ 越权问题（售后时效 / 员工旅游补贴）均正确拒答「资料库中未找到相关信息」，未被无关高分块（0.69-0.73）带偏。**实测结论：①用户抱怨的"内容不符合要求"主要是 mock 的锅，真模型下生成质量已可用，K3 范围应收窄为「上下文元信息 + 引用校验」；②最大痛点是 16.7s 零反馈（答案越长越久，qwen3.8-flash 是推理模型会先思考），K1 优先级被坐实；③检索召回质量（mock embedding）无法评测，需真 embedding 另测。** |
| 2026-09-26 | **阶段五（K 系列）排期 + 4 张需求卡片开卡（非功能块）** | 面向"真实用户第一次用会怎么想"做了一次完整体检，定位两条主线病根并逐一定位到行：**①内容不像人说的** —— `MockLLM`（`providers/mock.py:49-55`）设计上就不做总结、只摊原文事实句，属"没配 Key"而非功能缺失；真配 Key 后仍不足的部分归因于 `generator.py:11-23` 的 prompt 太简陋（无 system 角色 / 上下文无文档名与章节 / 无结构要求 / 拒答一刀切 / 无引用校验），且 `pipeline.py:105-113` 的文件名是**生成之后**才 attach，模型根本拿不到出处。**②一问就卡** —— `/ask/stream`（`chat.py:322-325` + `:335-341`）是**假流式**：先同步等完整答案（真模型 3–10s）再按 3 字符切片假装打字机；`_build_rag`（`chat.py:126-139`）**每请求重建管线**，每次都重开 PG 连接池 + 对 DashScope 重做 TLS 握手（300–800ms）；多轮下 `use_llm=True`（`query_rewriter.py:99`）每问前多一次 LLM 改写调用；摄取全程同步，`pipeline.py:82` 一次 embed 全部切片（100 页 PDF ≈ 25–40 次 HTTP 往返）。新增 `docs/roadmap.md` 阶段五（K1–K7，共 7 块）并开 4 张需求卡片：K1 真流式生成 / K2 管线单例与连接复用 / K3 答案质量 / K4 摄取异步化；另记录一处零代码快赢 —— `config.py:53` 默认 `chunk_strategy=fixed` 使块内不含章节标题（`chunker.py:196-210` 的 `structure` 策略会把标题作为块首），改配置即可提升模型引用条款号的能力。**本轮只做诊断与排期，未改任何功能代码，187 测试状态不变** |
