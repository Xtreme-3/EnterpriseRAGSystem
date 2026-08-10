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
| **合计** | | **35** | **35 ✅** |

> 你现在位置：V1 全部 30 块积木完成 🎉 —— 进阶功能见下方列表。

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

暂不拆块，等 V1 全部完成再排期。级别按 README「迭代路线图」：RBAC 权限、混合检索、Rerank、多轮对话、点赞/反馈日志、数据看板、管理后台（用户管理/系统配置/审计日志查询）、文档增量更新、异步摄取队列、向量库可插拔、多模型配置、审计日志、SSO/LDAP、更多格式、RPA 集成（Playwright 浏览器自动化 + RAG 智能决策）。
