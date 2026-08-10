# 系统架构

> 分层架构 + 两条核心数据流（摄取、问答）。
> **V1 只做基础功能**（实线框）；`[进阶]*` 为后续迭代，不阻塞 V1。

## 1. 分层架构（ASCII 版，任何查看器可见）

```text
┌───────────────────────────────────────────────────────────────────────────┐
│                       前端  Vue3 + Element Plus                            │
│   登录   ·   知识库管理   ·   文档上传   ·   对话(流式)   ·   [看板]*          │
└───────────────────────────┬───────────────────────────────────────────────┘
                            │ HTTPS + JWT
                            ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                      API 层  FastAPI                                      │
│   鉴权JWT  [RBAC]*   知识库接口   文档接口   问答接口(SSE)   [反馈]*  [看板]*  │
└───────────────────────────┬───────────────────────────────────────────────┘
                            │
                            ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                             核心引擎                                       │
│   摄取：  文档解析(PDF/TXT/MD) → 递归切片 → Embedding 向量化                  │
│   问答：  检索(纯向量) → [BM25混合]* → [Rerank]* → 生成LLM + 溯源引用         │
└────────────┬────────────────────────────────────────────────┬────────────┘
             │                                                │
             ▼                                                ▼
┌───────────────────────────┐               ┌───────────────────────────────┐
│      元数据  SQLite         │               │    向量库  ChromaDB            │
│  (知识库/文档/切片/用户)    │               │   每个知识库 = 独立集合 kb_{id} │
└───────────────────────────┘               └───────────────┬───────────────┘
                                                            │
                                   ┌────────────────────────┴────────────────┐
                                   │          模型供应商（可插拔）             │
                                   │   通义千问 DashScope  ·  智谱 GLM  · mock │
                                   └─────────────────────────────────────────┘

  [进阶]* = 后续迭代功能，V1 不实现（见 README「迭代路线图」）
```

## 2. 分层架构（mermaid 版，需支持 mermaid 的查看器，如 GitCode/GitHub 网页）

```mermaid
flowchart TB
    subgraph UI["前端 · Vue3 + Element Plus"]
        direction LR
        UI_LOGIN["登录"]
        UI_KB["知识库管理"]
        UI_DOC["文档上传"]
        UI_CHAT["对话（流式）"]
        UI_DASH["数据看板"]:::future
    end

    subgraph API["API 层 · FastAPI"]
        API_AUTH["鉴权 JWT"]
        API_RBAC["RBAC 授权"]:::future
        API_KB["知识库接口"]
        API_DOC["文档接口"]
        API_CHAT["问答接口（SSE 流式）"]
        API_FBK["反馈日志接口"]:::future
        API_DASH["看板接口"]:::future
    end

    subgraph CORE["核心引擎"]
        direction TB
        subgraph ING["摄取流水线"]
            ING_P["文档解析<br/>PDF / TXT / MD"]
            ING_C["递归切片 Chunker"]
            ING_E["Embedding 向量化"]
        end
        subgraph RAG["RAG 编排"]
            RAG_R["检索（V1 纯向量<br/>进阶+BM25 混合）"]:::future
            RAG_RR["重排序 Rerank（可选）"]:::future
            RAG_G["生成 LLM + 溯源引用"]
        end
    end

    subgraph DATA["数据层"]
        META[("元数据<br/>SQLite（可换 PostgreSQL）")]
        VEC[("向量库<br/>ChromaDB")]
        LOGS[("问答 / 反馈日志")]:::future
    end

    subgraph MODELS["模型供应商 · 可插拔"]
        M_Q["通义千问 DashScope"]
        M_Z["智谱 GLM"]
        M_MOCK["mock（离线）"]
    end

    UI_LOGIN --> API_AUTH
    UI_KB --> API_KB
    UI_DOC --> API_DOC
    UI_CHAT --> API_CHAT
    UI_DASH -.-> API_DASH
    API_RBAC -.-> API_KB

    API_DOC --> ING
    API_CHAT --> RAG
    API_FBK -.-> LOGS
    API_AUTH --> META

    ING --> VEC
    ING --> META
    RAG --> VEC
    RAG --> META
    RAG -.-> LOGS
    ING_E --> MODELS
    RAG_G --> MODELS

    classDef future stroke-dasharray: 5 5
    classDef db fill:#eef,stroke:#88b
    class META,VEC,LOGS db
```

## 3. 问答数据流

**流程（ASCII）**：用户 → 前端 → `POST /chat`(SSE) → 校验登录/权限 → 向量检索 top-k → 拼接上下文 → LLM 流式生成 → 返回答案 + 引用来源

```mermaid
sequenceDiagram
    participant U as 用户
    participant F as 前端
    participant A as API（FastAPI）
    participant R as RAG 引擎
    participant V as 向量库
    participant L as LLM 供应商

    U->>F: 输入问题
    F->>A: POST /chat（SSE 流式）
    A->>A: 校验登录 + 知识库权限
    A->>R: 检索（kb_id, query）
    R->>V: 向量检索 top-k
    V-->>R: 命中切片
    R->>L: 拼接上下文 + 引用编号
    L-->>R: 流式生成
    R-->>A: 流式文本 + 引用来源
    A-->>F: SSE 流
    F-->>U: 渲染答案 + 来源标注
```

## 4. 摄取数据流

```text
上传(PDF/TXT/MD) → 解析纯文本 → 递归切片(块+重叠) → Embedding → 写向量库+元数据 → 返回切片数/状态
```

```mermaid
flowchart LR
    UP["上传 PDF / TXT / MD"] --> PARSE["解析为纯文本"]
    PARSE --> CHUNK["递归切片（块+重叠）"]
    CHUNK --> EMBED["Embedding 向量化"]
    EMBED --> STORE["写向量库 + 元数据"]
    STORE --> STATUS["返回切片数 / 状态"]
```

## 5. 设计要点

- **知识库隔离**：每个知识库 = 独立向量集合（`kb_{id}`）+ 元数据按 `kb_id` 过滤，天然隔离互不串库
- **模型可插拔**：LLM / Embedding / Rerank 三层抽象，OpenAI 兼容接口对接通义/智谱，mock 离线可测
- **存储抽象**：VectorStore 接口（现 ChromaDB），进阶可换 pgvector / LanceDB；元数据 SQLAlchemy，可换 PostgreSQL
- **可扩展**：检索、重排、生成均为独立组件，混合检索 / Rerank 只需替换/插入对应积木，不动上层

> 💡 **为什么有 ASCII + mermaid 两份**：ASCII 图在任何 Markdown 查看器（本地编辑器、终端）都能显示；mermaid 图在支持它的平台（GitCode/GitHub 网页、VS Code 装「Markdown Preview Mermaid Support」插件后）会自动渲染成正式图形。若你本地想看图，用 GitCode 网页打开本文件，或给 VS Code 装 mermaid 预览插件即可。
