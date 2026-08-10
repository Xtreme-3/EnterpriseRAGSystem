# 需求卡片：B3-多知识库隔离（知识库 CRUD）

## 一句话
登录用户可以创建、查看、删除自己的知识库，数据按库隔离。

## 子块

| 子块 | 功能 | 说明 |
|------|------|------|
| B3-1 | 创建知识库 `POST /api/kbs` | name + description → 关联当前用户 |
| B3-2 | 知识库列表/详情 `GET /api/kbs` | 只返回当前用户的库；`GET /api/kbs/{id}` 详情含文档数 |
| B3-3 | 删除知识库 `DELETE /api/kbs/{id}` | 级联删除库内文档+切片+向量 |

## 输入 / 输出

- `POST /api/kbs` → `{"name": "...", "description": "..."}` → `{"id": 1, "name": "...", "description": "...", "created_at": "...", "document_count": 0}`
- `GET /api/kbs` → `[{"id": 1, "name": "...", ...}, ...]`
- `GET /api/kbs/{id}` → `{"id": 1, "name": "...", "documents": [...]}`
- `DELETE /api/kbs/{id}` → `{"status": "deleted"}`

## V1 不做什么

- 不做知识库共享/多用户协作
- 不做知识库编辑（只做创建+删除）
- 不做 RBAC 角色（owner/admin/viewer）

## 验收标准（可测试）

- [x] 创建知识库返回 201，包含 id/name/description/document_count
- [x] 未登录创建返回 401
- [x] 空名称返回 422（Pydantic 校验）
- [x] 列表只返回当前用户的知识库
- [x] 详情返回知识库含文档列表
- [x] 访问不存在的知识库返回 404
- [x] 访问别人的知识库返回 403
- [x] 删除知识库返回 200，级联删除文档+切片+向量
- [x] 删除别人的知识库返回 403
- [x] `pytest` 全部通过（34 测试全绿）

## 验证结果（2026-08-07）

- `app/api/kbs.py`：4 端点（create/list/detail/delete），Pydantic schemas，`_get_user_kb_or_403` 权限校验
- `app/core/models.py`：KnowledgeBase 新增 `user_id` FK（NOT NULL, ON DELETE CASCADE）
- `app/main.py`：挂载 kbs_router
- `app/ingestion/pipeline.py`：`create_kb` / `get_or_create_kb` 新增 `user_id` 参数
- `tests/test_kbs.py`：10 个测试（创建/空名称/未登录/列表隔离/详情/404/403交叉/删除/403交叉删除/未登录删除）
- `tests/test_pipeline.py`：fixture 新增默认用户，适配 user_id
- `scripts/demo.py`：新增 `_ensure_demo_user`，适配 user_id
- DB 迁移：knowledge_bases 表新增 user_id 列 + FK 约束 + 索引；移除 name unique 约束
- **34 测试全绿（24 + 10 新增 KB 测试）**

## 已知限制

- 同用户可创建同名知识库（V1 不做 name 唯一校验，不同用户的库天然隔离）
- 不支持知识库编辑/重命名

## 依赖

- 前置：B2（用户登录 + JWT），需要 `get_current_user` 鉴权
- 模型变更：KnowledgeBase 加 `user_id` 外键

## 失败点 / 风险

- KnowledgeBase 模型已有数据时需要 migration（当前开发阶段可删表重建）
- 级联删除向量数据需要向量库支持按 kb_id 批量删除
