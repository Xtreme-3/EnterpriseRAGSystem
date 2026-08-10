# 需求卡片：B2-用户登录 + JWT 会话

## 一句话
用户名 + 密码注册/登录，JWT 鉴权，为后续所有 API 积木提供统一的身份验证基座。

## 子块

| 子块 | 功能 | 说明 |
|---|---|---|
| B2-1 | 用户注册 `POST /api/auth/register` | username + password → bcrypt 哈希落库 |
| B2-2 | 登录 + JWT 签发 `POST /api/auth/login` | 验证凭据 → 签发 JWT（24h 过期） |
| B2-3 | 鉴权依赖 `get_current_user()` | 从 Authorization header 解析 JWT → 注入当前用户 |
| B2-4 | 获取当前用户 / 登出 | `GET /api/auth/me`（需登录）；`POST /api/auth/logout`（客户端删 token，V1 不做服务端黑名单） |

## 输入 / 输出

- `POST /api/auth/register` → `{"username": "...", "password": "..."}` → `{"id": 1, "username": "...", "created_at": "..."}`
- `POST /api/auth/login` → `{"username": "...", "password": "..."}` → `{"access_token": "...", "token_type": "bearer"}`
- `GET /api/auth/me` → `{"id": 1, "username": "...", "created_at": "..."}`（需 Authorization header）
- `POST /api/auth/logout` → `{"status": "ok"}`（V1 仅客户端删 token）

## V1 不做什么

- 不提供邮箱注册、邮箱验证、忘记密码/重置密码
- 不做 refresh token、token 黑名单/服务端登出
- 不做 RBAC/角色/权限（所有登录用户等价）

## 验收标准（可测试）

- [x] 注册成功返回用户信息，重复用户名返回 409
- [x] 登录成功返回 JWT，错误凭据返回 401
- [x] 有效 JWT 访问 `/api/auth/me` 返回用户信息
- [x] 无效/过期 JWT 返回 401
- [x] 无 Authorization header 返回 401
- [x] `pytest` 全部通过（含新增 B2 测试）

## 验证结果（2026-08-07）

- `app/core/models.py`：User 模型（username/hashed_password/created_at），B1 时已建表
- `app/api/auth.py`：4 端点（register/login/me/logout），bcrypt 哈希 + JWT HS256 签发
- `app/api/deps.py`：`get_current_user` 鉴权依赖（HTTPBearer + JWT decode + DB 查用户）
- `app/config.py`：`jwt_secret` + `jwt_expire_hours` 配置项
- `tests/test_auth.py`：8 个用例，覆盖全部 4 端点 + 异常路径
- 依赖安装：`python-jose[cryptography]`、`passlib[bcrypt]`、`bcrypt`
- **24 测试全绿（16 + 8 新增鉴权测试）**

## 依赖

- 前置：B1（FastAPI 骨架）
- 新增依赖：`passlib[bcrypt]`、`python-jose[cryptography]`

## 不做什么（边界）

- 不碰知识库/文档/问答（那是 B3–B6 的事）
- 不做用户管理（列表/删除/修改密码）——V1 之后