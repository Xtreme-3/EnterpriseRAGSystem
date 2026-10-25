# 需求卡片：L2-审计日志

> 排期依据：阶段六（2026-10-07 访谈定案），L1 之后第二块。实现前填好，做完在 `progress-log.md` 记状态。
> **状态：2026-10-25 实现完成 ✅** —— 决策①被实测推翻并修正，见下。

## 一句话

所有写操作（登录成败、注册、KB 增删、文档上传/删除/重试、成员增删改）落一条
append-only 审计记录；admin 在全局审计页分页查询 —— 补上「写操作零痕迹」的空白。

## 背景

查询行为已有 `QaLog`（自带 user_id）记账，但**写操作零痕迹**：谁删了库、谁把成员
从 viewer 提成 editor、谁在反复试密码（撞库），事后完全无法复盘。K8 只给登录失败留了
一条日志（文件里，非结构化、不可查询），且随滚动丢失。

## 关键设计决策（实现前定死，改动前先读）

1. **记账会话按路径二分**（⚠️ 2026-10-25 实测修正：最初设计「全程独立短会话」
   在 SQLite 下必撞 `database is locked` —— 成功路径上请求会话已经 flush 持有
   SQLite 写锁直到请求结束才 commit，第二个会话写不进去；失败还被 best-effort
   吞掉 = 审计**静默丢失**，被 `test_audit_filters_and_pagination` 的
   `assert total == 3` 抓住。教训同 L1：**设计先过实测**）：
   - ``record_audit(db, ...)`` —— **成功路径**：写入请求会话，与业务**同事务**提交。
     语义反而更正确：操作成功 ⇔ 留审计，业务回滚审计同亡（没做成的事不留成功假象）；
   - ``record_audit_detached(request, ...)`` —— **失败路径**（目前仅 401 登录失败）：
     独立短会话立即提交，因为路由随后 raise 会触发 ``get_db`` 的 rollback，挂在
     请求会话上的失败审计会跟着消失。此路径请求会话只做过 SELECT（无写锁），
     短会话写入无冲突。best-effort：审计失败只记 exception 日志，绝不改变业务响应。
2. **不设 FK、不级联删除**。审计是历史事实：删库恰恰是最需要留痕的动作，KB 删除
   不能带走它自己的删除记录。对比：`QaLog` 随 KB 级联删除（「日志无意义」语义），
   审计相反。`kb_id` / `actor_id` 是裸 `Integer`（可空、无 ForeignKey），靠
   `actor_name` / `target` 里的名字保证删库删用户后记录仍可读。
3. **append-only**：不提供删除 / 清空 / 修改接口。表不加 `updated_at`。
4. **actor 双字段**：`actor_id`（可空）+ `actor_name`（恒填）。登录失败时用户可能
   不存在（撞库探测），此时 `actor_id=NULL`、`actor_name=<尝试的用户名>` —— 这条
   信息只有 actor_name 能承载。
5. **`detail` 是给人看的短字符串，不是结构化 JSON**。所有可能用于过滤的维度
   （action / actor_id / kb_id）都有独立列，别把可过滤信息塞进 detail。

## 记账范围与 action 清单（11 个）

| action | 挂点（文件） | actor | kb_id | target | detail |
|---|---|---|---|---|---|
| `auth.login` | auth.py 登录成功 | 登录者 | — | — | — |
| `auth.login_failed` | auth.py 401 前 | 可空 | — | — | `reason=用户不存在/密码错误`（**绝不记密码**，K8-4 同约定） |
| `auth.register` | auth.py 注册成功 | 新用户 | — | `user:{id}({username})` | — |
| `kb.create` | kbs.py create_kb | 创建者 | 新库 id | `kb:{id}({name})` | — |
| `kb.delete` | kbs.py delete_kb（**删前记**） | 执行者 | 被删库 id | `kb:{id}({name})` | — |
| `document.upload` | documents.py upload（202 受理即记，不等摄取终态） | 上传者 | 库 id | `document:{id}({filename})` | — |
| `document.delete` | documents.py delete_document | 执行者 | 库 id | `document:{id}({filename})` | — |
| `document.retry` | documents.py retry（enqueue 成功后记） | 执行者 | 库 id | `document:{id}({filename})` | — |
| `member.add` | kbs.py add_member | 操作者 | 库 id | `user:{id}({username})` | `role={editor/viewer}` |
| `member.role_change` | kbs.py update_member | 操作者 | 库 id | `user:{id}({username})` | `role={旧}→{新}` |
| `member.remove` | kbs.py remove_member | 操作者 | 库 id | `user:{id}({username})` | — |

语义色映射（前端 Tag）：**danger** = kb.delete / document.delete / member.remove /
auth.login_failed；**success** = kb.create / document.upload / auth.register /
member.add；**info** = 其余（auth.login / document.retry / member.role_change）。
一屏三种语义色封顶（DESIGN.md §二）。

## 查询 API

`GET /api/audit-logs` —— **仅 admin**（`deps.require_admin`，非 admin 403）。
参数：`action`（精确匹配）/ `actor_id` / `kb_id` / `limit`（默认 50，≤200）/
`offset`；返回 `{items, total}`，`created_at desc, id desc`。
本仓第一个全局（非 KB 作用域）接口：QaLog / Dashboard 都是 KB 级 viewer+，
审计是 admin 全局视角，**两者不重叠、不互相替代**。

## 前端

- 路由 `/audit` + `meta.requiresAdmin` 守卫（非 admin 重定向 `/kbs`；守卫与后端
  403 是两道独立的门，前端挡的是误入，后端挡的是越权）
- 入口：`DefaultLayout` 顶栏 admin-only 文字按钮「审计日志」（全站唯一全局入口，
  本仓无侧边栏，顶栏是唯一常驻导航位）
- 页面形状 = **表格页**（DocList / QaLogs 之后第三张）：action 过滤下拉 + 分页 +
  状态 Tag + 三态加载（K8：加载中 / 成功 / 失败）+ `extractErrorMessage` 兜底
- 宽度 1200px（列多：时间 / 操作 / 操作人 / 对象 / 详情，不适合 QaLogs 的 960 窄版）

## 验收标准（可测试，逐条打勾）

- [x] 11 个写点各产出一条正确 action 的审计（真实 API 流程触发，非直插 DB）
- [x] **401 登录失败也落库**（决策 ① 失败路径的直接验证——挂在请求会话上必被 rollback 吞掉）
- [x] **删库后该库的审计记录幸存**（决策 ② 的直接验证）
- [x] 失败路径的审计挂掉不影响业务：`AuditLog` 构造抛错替身下，登录失败仍 401 不 500
      （该用例还顺手抓到 `_build_log` 在 try 块外的真实缺陷）
- [x] 非 admin 查询 403；admin 可查；`action` 过滤与分页 `total` 正确
- [x] 后端全量 pytest 0 failed（新增 9 例**全离线**：DATA_DIR → tmp_path，不动 PG-gated 约定）
- [x] 前端 vitest 全绿（新页 spec + 路由守卫 spec），`vue-tsc` 与 build 通过
- [x] 新增 spec 断言做过反向验证（K9 约定：临时移除 requiresAdmin 守卫分支，
      `非 admin 访问 /audit 时重定向 /kbs` 变红后恢复）

## 实测数字（2026-10-25，全离线）

- 后端：**482 collected / 369 passed / 0 failed / 113 skipped**（473 → 482）
- 前端：**74 tests / 11 files 全绿**（63 → 74：AuditLogs 页 5 例 + auditApi 契约 3 例 + 路由守卫 3 例），
  `vue-tsc && vite build` exit 0
- 路由守卫反向验证：移除分支 → 1 failed → 恢复 → 8/8

## 依赖

- 前置积木：I2（`User.role` 全局 admin + admin 旁路）、B2 / B3 / B4 / B5（各写点）、
  K8（三态加载 + `extractErrorMessage`）、K9（前端测试框架）

## 不做什么（边界，防止范围蔓延）

- 不做审计的修改 / 删除 / 清空（append-only 是产品语义，不是偷懒）
- 不做时间范围过滤、导出 CSV、审计数据保留期 / TTL 配置（需要时另开卡）
- 不动 `QaLog`：查询行为已有记账，本块不重复记 `ask` / `chat`
- 不做「审计页嵌入 KB 视图」：审计是全局视角，与 KB 级页面分开
- 不改既有页面的任何交互

## 失败点 / 风险

- **SQLite 并发写锁**：已按决策 ① 的二分设计规避（成功路径单会话、失败路径请求会话
  无写锁）；若未来把 ``record_audit_detached`` 用到「请求会话已 flush」的路径上，
  会重现 `database is locked` —— 那是误用，不是本设计的漏洞
- **action 清单是两处维护**：后端 `app/core/audit.py` 的常量与前端页面的中文映射表
  —— 后端新增 action 忘改前端时，Tag 显示原始 action 名（可接受降级，不报错，
  有 spec 钉住这个降级行为）
- **审计表增长无界**：demo 规模不设保留期（见「不做什么」）；真上生产需另开卡
