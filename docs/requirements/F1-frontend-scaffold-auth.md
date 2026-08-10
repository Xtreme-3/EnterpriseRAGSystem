# 需求卡片：F1-前端脚手架 + 登录/注册

## 一句话
搭建 Vue3 + Vite 前端项目骨架，实现登录和注册页面，对接后端 JWT 鉴权接口。

## 子块

| 子块 | 功能 | 说明 |
|------|------|------|
| F1-1 | 项目脚手架 | Vite + Vue3 + Element Plus + vue-router + axios |
| F1-2 | 登录页 | `/login` — 用户名+密码 → JWT 存储 → 跳转 |
| F1-3 | 注册页 | `/register` — 用户名+密码 → 自动登录 → 跳转 |

## 输入 / 输出

- 登录页：输入用户名+密码 → `POST /api/auth/login` → 存储 access_token → 跳转 `/kbs`（F2 实现）
- 注册页：输入用户名+密码 → `POST /api/auth/register` → 成功则自动登录 → 跳转 `/kbs`
- 未登录用户访问任何受保护路由 → 跳转 `/login`

## 验收标准（可测试）

- [ ] `npm run dev` 启动成功
- [ ] `/login` 页面正常渲染（Element Plus 表单）
- [ ] `/register` 页面正常渲染
- [ ] 注册新用户 → 跳转 `/kbs`（当前可跳转到占位页）
- [ ] 已有用户登录 → 跳转 `/kbs`
- [ ] 错误提示（用户名已存在、密码错误、网络错误）
- [ ] 路由守卫：未登录访问受保护页面 → 跳转 `/login`
- [ ] 后端 55 个测试仍然通过

## 依赖

- 前置：阶段二全部（B1–B6），后端 API 就绪
- 技术栈：Vue3 + Vite + Element Plus + vue-router + axios + pinia

## 不做什么（边界，防止范围蔓延）

- 不做知识库列表/管理页面（F2）
- 不做布局/导航栏（F2 加 DefaultLayout）
- 不做"记住我"/自动登录/刷新 token
- 不做前端测试（V1 阶段手动验证 + 后端 API 测试覆盖）

## 失败点 / 风险

- CORS：前端 dev server (localhost:5173) 跨域调用后端 (localhost:8000)，需要在 vite.config 配置代理
- 后端 dev 依赖 SQLite 文件，需确保 data/ 目录存在
