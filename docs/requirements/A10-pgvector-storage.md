# A10：存储层切换 PostgreSQL + pgvector（元数据 + 向量一套库）

## 目标

将当前 SQLite（元数据） + ChromaDB（向量） 双存储切换为 **PostgreSQL + pgvector 一套库**，元数据与向量共库管理。

## 动机

- 企业 RAG 标配技术栈，简历含金量
- 一套库管业务元数据与向量，简化运维
- tsvector 原生全文检索，支撑进阶「混合检索」
- 保留 VectorStore 接口抽象，切换只改后端实现

## 前置条件

- [x] A1–A9 已完成
- [x] PostgreSQL + pgvector 可用（**Docker 容器 `pgvector/pgvector:pg17`**，映射 5432，避免 Windows 原生编译；Docker Hub 被墙时经 `docker.1ms.run` 镜像源拉取）
- [x] 安装 psycopg2-binary + pgvector（Python 驱动/客户端，均已入 `pyproject.toml`）

## 具体要做的事

### 1. 安装 PostgreSQL + pgvector
- 用 winget 安装 PostgreSQL 16（`winget install PostgreSQL.PostgreSQL.16`）
- 安装 pgvector 扩展（从 GitHub releases 下载 Windows 预编译 `.dll`，放入 `lib/` 目录）
- 创建数据库 `ragdb`，启用 pgvector 扩展
- 用户自行设定 postgres 密码，写入本地 `.env`（不进版本控制）

### 2. 更新依赖与配置
- `pyproject.toml`：添加 `psycopg2-binary`、`pgvector`（Python 客户端）
- `app/config.py`：新增 `database_url` 字段（PostgreSQL DSN），移除 `sqlite_url` / `chroma_dir`
- `.env.example`：新增 PostgreSQL 连接配置项

### 3. 更新元数据存储层
- `app/storage/db.py`：`init_db()` 支持 PostgreSQL engine（psycopg2），不再依赖 SQLite
- `app/core/models.py`：检查模型兼容性（必要时调整列类型/Mapped 注解）

### 4. 实现 PgVectorStore
- 新建 `app/storage/pgvector_store.py`：实现 `VectorStore` 接口
  - `ensure_collection`：创建 pgvector 扩展 + 向量表（每知识库一张表或单表 kb_id 分区）
  - `add`：INSERT ... ON CONFLICT 实现 upsert
  - `search`：`<=>` 余弦距离算子，`1 - (embedding <=> query_vector)` 转相似度
  - `delete_document` / `delete_collection`：按条件删除
- 更新 `app/storage/vector_store.py` 的 `build_vector_store()`，支持 `vector_store=pgvector`

### 5. 向后兼容
- ChromaDB 实现保留不动（`vector_store=chroma` 仍可用），不影响现有测试
- 测试用 `vector_store=chroma`（无需 PostgreSQL），保证 CI 离线可跑
- 新增 pgvector 专项测试（仅当 PostgreSQL 可用时运行）

### 6. 验证
- 手动启动 PostgreSQL，创建 ragdb 库
- 运行 `scripts/demo.py`（将 VECTOR_STORE 改为 pgvector）
- 运行 `pytest`（现有测试应全部通过，测试仍用 chroma）
- 新增 pgvector 集成测试

## 验收标准

- [x] `VECTOR_STORE=pgvector` 时，`build_vector_store()` 返回 PgVectorStore 实例
- [x] `init_db()` 在 PostgreSQL 模式下正常建表（元数据三表）
- [x] `demo.py` 用 pgvector 模式跑通全链路（mock 供应商）
- [x] `pytest` 全部通过（chroma 测试不受影响；新增 pgvector 集成测试，PG 不可达时跳过）
- [x] `.env.example` 包含 PostgreSQL 配置说明

## 验证结果（2026-08-06）

- `docker run` 容器启动成功，`vector 0.8.6` 扩展可用
- `demo.py` 全链路跑通：建库 → 摄取 3 切片 → 检索（相似度 0.540）→ 答案 + 引用；重复运行幂等跳过
- **修复 3 个 pgvector 路径 bug**（此前测试全走 chroma，pgvector 代码从未执行，首次实跑暴露）：
  1. `register_vector()` 缺必填 `conn_or_curs` 参数 → 传 `conn.connection.driver_connection`，`globally=True`
  2. `register_vector` 需 `vector` 类型已存在 → 移到 `CREATE EXTENSION` 之后
  3. psycopg2 把 `list` 渲染成 `{…}` 而 vector 类型只接受 `[ … ]` → 绑参包 `pgvector.Vector(...)`
- 测试 fixture 钉死 `vector_store="chroma"`（.env 切到 pgvector 后保持测试离线自包含）
- **13 测试全绿**（12 原 + 1 pgvector 集成）

## 依赖关系

- 被依赖：B1（FastAPI 骨架）依赖 A10 完成后的存储层
- 依赖：A1–A9（已全部完成）

## 风险

- pgvector Windows 安装可能不顺利（预编译 dll 可能不兼容），备选方案：ChromaDB 用 PostgreSQL 作为后端（通过 chromadb 的 settings 配置），但这不是"一套库"方案
- 若 PostgreSQL 安装受阻，可先跳过安装步骤，**仅完成代码实现**（pgvector_store.py + 配置），待安装成功后再验证