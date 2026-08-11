# 需求卡片：I2-RBAC 权限（知识库级角色）

> RAG 进阶。定位：**多人协作 + 最小权限**——把当前"KB 归属单用户"（`kb.user_id`）
> 升级为"知识库成员 + 角色（owner/editor/viewer）"，写操作按角色拦截，
> 让协作成员（如只读审查、内容编辑）加入同一知识库，而不会越权。

## 一句话

`User` 加全局角色（`user`/`admin`），新增知识库成员表（角色 `owner`/`editor`/`viewer`）；
所有 KB 级接口从"只看 owner"改为"按角色判定"，owner 可管理成员，admin 全局旁路。

## 背景与动机

B3 建立"用户只能操作自己的知识库"（`_get_user_kb_or_403` 检查 `kb.user_id`）。
简历项目要体现"权限设计"，且真实场景常需要：让同事只读审查某库、让编辑协助补文档，
而删除库/管成员这类高危操作只有 owner 能做。README 迭代路线图列为 ⭐⭐⭐ RBAC 权限。

## 方案

- **数据模型**（`app/core/models.py`）：
  - `User.role: str`（`user` | `admin`，默认 `user`）
  - 新表 `KnowledgeBaseMember`：`kb_id`（FK cascade）、`user_id`（FK cascade）、
    `role`（`editor` | `viewer`）、`created_at`；`UniqueConstraint(kb_id, user_id)`
  - **owner 不入成员表**：owner = `KnowledgeBase.user_id`（创建者，权威来源）。
    老库无需迁移即可识别 owner；成员表只存协作成员。成员列表接口 UNION owner。
- **迁移**（`app/storage/db.py::init_db`）：新表由 `create_all` 建；PG 老 `users` 表
  加列用幂等 `ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'user'`
  （复用 H1 content_tokens 的 IF NOT EXISTS 模式；SQLite 测试用全新库自动包含）。
- **角色判定**（`app/api/kbs.py`）：
  - `ROLE_LEVEL = {"viewer": 1, "editor": 2, "owner": 3}`
  - `get_user_kb_role(db, kb, user) -> str | None`：`admin → owner`；
    `kb.user_id == user.id → owner`；成员表命中 → 其角色；否则 `None`（无权）
  - `_get_user_kb_or_403(db, kb_id, user, required="viewer")`：保持原签名与 404/403
    语义，新增 `required` 门槛（级别不够 → 403 权限不足）。**默认 viewer → 现有调用零改动**。
- **权限收紧**（写操作按角色）：
  - 上传文档 / 删除文档 → `editor+`
  - 删除知识库 → `owner`
  - 其余读接口（详情/文档列表/状态/问答/质检/体检/健康/历史）→ `viewer+`
    （成员自动获得，不再只有 owner）
  - admin 视为全局 owner（可访问/删除任意库、管任意库成员）
- **列表可见性**：`list_kbs` 返回"我拥有 + 我作为成员"的所有库（admin 返回全部），
  每条带 `role`（我对此库的角色）；`get_kb` 详情也带 `role`。
- **成员管理**（新路由 `/api/kbs/{id}/members`，owner 才能写）：
  - `GET`（editor+）：owner 行 + 成员列表（`{user_id, username, role, created_at}`）
  - `POST`（owner）：`{username, role: editor|viewer}` 添加成员（用户不存在 404；已是 owner/成员 409）
  - `PATCH /{user_id}`（owner）：改成员角色（editor↔viewer）；owner 行不可改（400）
  - `DELETE /{user_id}`（owner）：移除成员；owner 行不可移（400）
- **auth**：`/auth/me` 与注册响应带 `role`；注册默认 `user`。

## 子块

| 子块 | 功能 | 说明 |
|------|------|------|
| I2-1 | 数据模型 + 迁移 | `User.role`、`KnowledgeBaseMember`、PG 幂等 ALTER |
| I2-2 | 角色判定依赖 | `get_user_kb_role` / `_get_user_kb_or_403(required)` / admin 旁路 |
| I2-3 | 接口权限收紧 | delete=owner；upload/delete_doc=editor；list_kbs 成员可见 + role |
| I2-4 | 成员管理 API | GET/POST/PATCH/DELETE members + 校验 |
| I2-5 | auth + 前端 | `/me` 带 role；KB 卡片角色徽章、DocList 按钮门控、成员管理对话框 |

## 输入 / 输出

- `User.role: "user" | "admin"`（默认 user）
- `KnowledgeBaseMember(kb_id, user_id, role: "editor" | "viewer")`
- `_get_user_kb_or_403(db, kb_id, user, required="viewer") -> KnowledgeBase`
- `GET /api/kbs/{id}/members` → `[{user_id, username, role, created_at}]`（owner 在前）
- `POST /api/kbs/{id}/members` body `{username, role}` → 201 成员
- `PATCH /api/kbs/{id}/members/{user_id}` body `{role}`；`DELETE /api/kbs/{id}/members/{user_id}`
- `GET /api/kbs` 每条含 `role`；`GET /api/auth/me` 含 `role`

## 验收标准（可测试）

- [x] 注册默认 `role=user`；`/me` 返回 `role`；admin 只能由库内/后续管理接口设置
- [x] owner：创建/列表/详情/删除 KB + 管理成员，`list_kbs` 中该库 `role=owner`
- [x] viewer：可读 KB/文档/问答/质检/体检/历史；上传/删除文档、删除 KB、管理成员均 403
- [x] editor：可上传/删除文档；删除 KB、管理成员仍 403
- [x] 非成员访问 KB 详情/文档/问答 403；`list_kbs` 不出现他人库（回归）
- [x] 成员管理：添加（404 用户不存在 / 409 重复或 owner）、改角色、移除；owner 行改/移 400；非法 role 422
- [x] viewer 成为成员后 `list_kbs` 出现该库且 `role=viewer`；editor 同理
- [x] admin 访问任意库视为 owner（可删任意库、可管成员）
- [x] 现有 140 测试保持全绿 + 新增 rbac 测试
- [x] `npm run build` 通过

## 依赖

- 前置：B2（鉴权）、B3（KB CRUD）、B4/B5（文档写接口）
- 复用：`_get_user_kb_or_403`（加 required 参数）、`get_current_user`、PG 幂等迁移模式、
  测试 fixture 钉死 chroma/SQLite、PG 集成测试（test_inspect 模式）
- 新增：无新依赖

## 不做什么（边界，防止范围蔓延）

- 不做所有权转移（owner 固定为创建者；转让留后续积木）
- 不做 admin 管理后台（用户列表/封禁/改全局角色）——`role` 字段已预留，界面放后续积木
- 不做邀请链接 / 审批流——直接按 username 添加已注册用户
- 不做前台开放 admin 注册——admin 通过 DB 或后续管理接口授予
- 不做 KB 改名/改描述（当前无该接口，owner 能力由删除+成员管理覆盖；改名留后续）
- 不做前端硬隔离权限（按钮按 role 隐藏仅为体验），强制校验在后端

## 失败点 / 风险

- 老 PG 库 `users` 表无 `role` 列 → init_db 幂等 ALTER（IF NOT EXISTS），勿用 create_all 硬改
- owner 不在 members 表 → 权限判定先查 `kb.user_id`，成员列表接口 UNION owner，测试覆盖
- 写接口漏收权限 → 逐端点核对 documents/kbs 写操作加 required，测试覆盖 403 矩阵
- admin 旁路语义需明确 → 文档写明 admin=owner（含删除任意库），测试验证
- 重复添加 / 改 owner 等边界 → 后端 409/400 兜底，前端对话框二次提示
