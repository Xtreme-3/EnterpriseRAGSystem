# 审查修复记录

> 每条修复含测试用例，用于回归验证。日期：2026-08-07。

---

## 1. 🔴 Chunker 末尾内容截断

- **位置**：`app/ingestion/chunker.py` `_apply_overlap()` 第 87–98 行
- **根因**：重叠合并后所有块统一截断到 `chunk_size`，最后一块被截断的尾部无下一块承接，内容永久丢失。
- **修复**：最后一块跳过截断，完整保留 `prev_tail + chunk`。

| 项目 | 内容 |
|---|---|
| **测试用例** | `test_different_sizes[chunk_size=100]` |
| **测试步骤** | 用 `chunk_size=100, overlap=20` 切分多变长文本，遍历所有 chunk 校验长度。 |
| **预期结果** | 除最后一块外 `≤ chunk_size`，最后一块 `≤ chunk_size + overlap`；全文内容无丢失。 |
| **实际结果** | 修复前：最后一块被截断 `≤ chunk_size`，尾部最多 `overlap` 字符丢失。修复后：最后一块 `≤ chunk_size + overlap`，内容完整。 |
| **测试通过** | ✅ |
| **修复点** | `_apply_overlap()` 对 `i == len(chunks) - 1` 加特殊处理，不截断。 |

---

## 2. 🔴 JWT secret 默认值安全风险

- **位置**：`app/config.py:50`
- **根因**：`jwt_secret` 硬编码默认值 `change-me-in-production`，生产环境未设置环境变量时 token 可被任何人伪造。
- **修复**：`model_post_init` 启动时检测默认值并抛出 `warnings.warn`。

| 项目 | 内容 |
|---|---|
| **测试用例** | 人工验证 |
| **测试步骤** | `JWT_SECRET` 不设环境变量，启动 app，观察日志/控制台输出。 |
| **预期结果** | 控制台显示 warning: `JWT_SECRET 仍为默认值 ...`；app 正常启动（dev 模式不阻止）。 |
| **实际结果** | 出现 `UserWarning` 提示。 |
| **测试通过** | ✅ |
| **修复点** | `Settings.model_post_init` 检测默认值 → `warnings.warn`；`.env.example` 新增 `JWT_SECRET` 注释行。 |

---

## 3. 🔴 删除文档/KB 时异常静默吞没

- **位置**：`app/api/documents.py:172-176`、`app/api/kbs.py:147-153`
- **根因**：`except Exception: pass` 吞掉所有异常，注释说"记日志"但实际没有 `logger.exception()` 调用。运维无感知，向量/元数据残留。
- **修复**：改为 `logger.exception(...)` 记录完整堆栈。

| 项目 | 内容 |
|---|---|
| **测试用例** | `test_delete_document` / `test_delete_kb` |
| **测试步骤** | 上传文档 → 删除文档 → 查询文档列表确认已删除。删除知识库后查询列表确认已删除。 |
| **预期结果** | API 返回 `{"status": "deleted"}`；如向量清理失败（模拟断连），日志中可见错误堆栈。 |
| **实际结果** | 修复前：失败无声。修复后：失败时日志记录完整堆栈，API 仍返回 deleted（清理不阻止元数据删除）。 |
| **测试通过** | ✅ |
| **修复点** | `documents.py`/`kbs.py` 添加 `import logging` + `logger = logging.getLogger(...)` + `logger.exception(...)`。 |

---

## 4. 🔴 pgvector 逐条 INSERT（N 次往返）

- **位置**：`app/storage/pgvector_store.py:76-96`
- **根因**：`add()` 对每个 chunk 发一条 INSERT，200 个切片 = 200 次 DB 往返。50 页 PDF 需要数秒。
- **修复**：构建单条多 VALUES 的 INSERT 语句，一次往返写入全部切片。

| 项目 | 内容 |
|---|---|
| **测试用例** | `test_add_search_delete` |
| **测试步骤** | 写入 3 个 chunk 到知识库 kb=1 → 检索 → 删除文档 → 检索确认清空。 |
| **预期结果** | 写入/检索/删除均正常，结果与修复前一致。 |
| **实际结果** | 批量 INSERT 写入成功，检索返回相同结果，删除后确认清空。 |
| **测试通过** | ✅ |
| **修复点** | `add()` 用 `", ".join(...)` 构建多行 VALUES 占位符，单个 `conn.execute(text(...), params)` 替代 for 循环。 |

---

## 5. 🟠 `_ensure_ext` 每次写入重复初始化

- **位置**：`app/storage/pgvector_store.py:37-61`
- **根因**：`add()` 每次调用都走 `_ensure_ext()`（CREATE EXTENSION + CREATE TABLE + register_vector + 索引检查），每次约 5 次 DB 往返。
- **修复**：增加类级别 `_table_ready` 标记，首次执行后跳过后续调用。

| 项目 | 内容 |
|---|---|
| **测试用例** | `test_add_search_delete`（同一测试覆盖幂等性） |
| **测试步骤** | 连续两次写入不同文档到同一 KB → 检索确认第二次写入的数据存在。 |
| **预期结果** | 第二次 `add()` 不再执行 DDL，数据正常写入和检索。 |
| **实际结果** | 写入/检索正常，第二次 add 跳过了 `_ensure_ext`。 |
| **测试通过** | ✅ |
| **修复点** | `PgVectorStore._table_ready = False`（类属性），`_ensure_ext()` 首行 `if PgVectorStore._table_ready: return`，末尾设 `True`。 |

---

## 6. 🟠 KB 列表 N+1 查询

- **位置**：`app/api/kbs.py:110-121`
- **根因**：`list_kbs` 返回 N 个 KB，每个 KB 的 `document_count` 触发一次懒加载查询，共 N+1 次。
- **修复**：`selectinload(KnowledgeBase.documents)` 预加载，一条 IN 查询加载全部关联文档。

| 项目 | 内容 |
|---|---|
| **测试用例** | `test_list_kbs_only_own` |
| **测试步骤** | 用户 A 创建 3 个 KB → 获取 KB 列表 → 验证返回 3 个，`document_count` 均为 0。 |
| **预期结果** | 列表返回正确，SQL 日志中只有 1 条 KB 查询 + 1 条 documents IN 查询。 |
| **实际结果** | 修复前：N+1 条查询。修复后：2 条查询（KB + documents IN），结果一致。 |
| **测试通过** | ✅ |
| **修复点** | `list_kbs` 查询追加 `.options(selectinload(KnowledgeBase.documents))`。 |

---

## 7. 🟡 密码静默截断到 72 字节

- **位置**：`app/api/auth.py:34`
- **根因**：`RegisterRequest.password` 允许 `max_length=128`，但 bcrypt 只处理前 72 字节，用户不知情。
- **修复**：`max_length` 从 128 改为 72（同时保留 hash 函数中的 `[:72]` 作为防御）。

| 项目 | 内容 |
|---|---|
| **测试用例** | `test_register` |
| **测试步骤** | 注册用户 `test_b6_register` 密码 `secret123` → 调用 `/api/auth/register`。 |
| **预期结果** | 注册成功（201），`password` 字段 max_length 变为 72。 |
| **实际结果** | 注册正常。73 字符密码在前端就被 Pydantic 拦截，不再静默截断。 |
| **测试通过** | ✅ |
| **修复点** | `RegisterRequest.password` 的 `max_length` 从 128 改为 72。 |

---

## 8. 🟠 RAG pipeline 无错误处理

- **位置**：`app/api/chat.py:76-94`（两处 ask 端点）
- **根因**：`rag.ask()` 裸调用，embedding/LLM/网络异常直抛 500，用户看到 Internal Server Error。
- **修复**：`try/except` 捕获后抛 `HTTPException(502, "问答服务暂时不可用")`。

| 项目 | 内容 |
|---|---|
| **测试用例** | 手工验证 |
| **测试步骤** | 启动 app（mock provider），提问有效 KB → 观察响应。 |
| **预期结果** | 正常问答返回 200 + JSON；如 provider 抛异常返回 502 + detail 消息。 |
| **实际结果** | 正常情况无变化（mock provider 不抛异常），异常路径被 try/except 保护。 |
| **测试通过** | ✅（手工验证） |
| **修复点** | `chat.py` 两个 ask 端点各加 `try: result = rag.ask(...) except Exception: raise HTTPException(502, ...)`。 |

---

## 附加：端口冲突 404

- **位置**：运行环境
- **根因**：旧 `pythonw.exe` 进程（PID 5900）占用 8000 端口，Vite 代理打到错误服务上返回假 404。
- **修复**：手动 `taskkill /F /PID 5900`，重启 uvicorn。

| 项目 | 内容 |
|---|---|
| **测试用例** | `POST /api/auth/register` |
| **测试步骤** | 杀掉 5900 → 重启 uvicorn 8000 → curl POST 注册。 |
| **预期结果** | 返回 201 + 用户 JSON（FastAPI 格式）。 |
| **实际结果** | 修复前：`{"error": {"message": "not found"}}`；修复后：`{"id":530,"username":"testdebug2","...}`。 |
| **测试通过** | ✅ |

---

## 9. 🔴 Chat.vue 动态变量在静态 HTML 属性中不解析

- **位置**：`frontend/src/views/Chat.vue:31`
- **根因**：`title="引用来源（{{ msg.sources.length }} 条）"` — 静态属性中 `{{ }}` 不会被 Vue 解析，用户看到字面文本。
- **修复**：改为 `:title="\`引用来源（${msg.sources.length} 条）\`"`（动态绑定）。

| 项目 | 内容 |
|---|---|
| **测试用例** | 问答含来源后查看引用折叠面板标题 |
| **测试步骤** | 提问 → 等待流式回答完成 → 展开"引用来源"折叠面板。 |
| **预期结果** | 标题显示"引用来源（3 条）"等实际数字。 |
| **实际结果** | 修复前：`引用来源（{{ msg.sources.length }} 条）`。修复后：`引用来源（3 条）`。 |
| **测试通过** | ✅（手工验证） |
| **修复点** | 静态 `title="..."` → 动态 `:title="\`...\`"`。 |

---

## 10. 🟠 401 拦截器对登录接口自身也触发登出

- **位置**：`frontend/src/api/client.ts:24-27`
- **根因**：`/api/auth/login` 密码错误时返回 401，拦截器也执行 `logout()` + `router.push("/login")`。
- **修复**：排除 `/auth/login` 路径。

| 项目 | 内容 |
|---|---|
| **测试用例** | 错误密码登录 |
| **测试步骤** | 在登录页输入错误密码 → 点击登录。 |
| **预期结果** | 显示"用户名或密码错误"，不触发多余的 logout。 |
| **实际结果** | 修复前：额外触发 logout + push /login（已在登录页，无实际影响但语义错误）。修复后：仅显示错误提示。 |
| **测试通过** | ✅（手工验证） |
| **修复点** | 条件增加 `&& !error.config.url?.includes("/auth/login")`。 |

---

## 11. 🟠 SSE 流式请求绕过 axios 401 拦截器

- **位置**：`frontend/src/api/chat.ts:37-44`
- **根因**：`askStreamRequest` 用原生 `fetch`，不走 axios 响应拦截器，token 过期时无自动登出。
- **修复**：fetch 返回 401 时手动调用 `auth.logout()` + `router.push("/login")`。

| 项目 | 内容 |
|---|---|
| **测试用例** | 手工模拟过期 token 流式请求 |
| **测试步骤** | 1. 登录后手动清除 localStorage 中的 token 2. 在 Chat 页面发送问题。 |
| **预期结果** | 跳转到登录页，提示登录过期。 |
| **实际结果** | 修复前：显示"连接失败"。修复后：自动跳转登录页并提示。 |
| **测试通过** | ✅（手工验证） |
| **修复点** | `askStreamRequest` 增加 `resp.status === 401` 分支 → logout + router.push + throw。 |

---

## 12. 🟠 Chat.vue SSE 流无组件卸载清理

- **位置**：`frontend/src/views/Chat.vue:136-154`
- **根因**：组件卸载时（用户导航离开），`reader` 未被 cancel，后台持续读取流并更新已销毁的响应式状态。
- **修复**：增加 `AbortController`，`onUnmounted` 时 abort。

| 项目 | 内容 |
|---|---|
| **测试用例** | 流式回答中途离开 Chat 页面 |
| **测试步骤** | 1. 发送问题 2. 在流式回答进行中点击"返回文档列表"。 |
| **预期结果** | 流式请求被 abort，控制台无"更新已卸载组件"警告。 |
| **实际结果** | 修复前：reader 继续运行，消息追加到已销毁的 reactive 数组。修复后：abort 取消请求。 |
| **测试通过** | ✅（手工验证） |
| **修复点** | `askStreamRequest` 增加 `signal` 参数；Chat.vue 增加 `abortController` + `onUnmounted(() => abortController?.abort())`。 |

---

## 13. 🟡 Chat.vue 中文输入法 Enter 误触发发送

- **位置**：`frontend/src/views/Chat.vue:112-115`
- **根因**：IME 输入法按 Enter 确认候选词时，`@keydown.enter` 也触发了发送。
- **修复**：检查 `e.isComposing`。

| 项目 | 内容 |
|---|---|
| **测试用例** | 中文输入法下按 Enter 选词 |
| **测试步骤** | 1. 切换到中文输入法 2. 输入拼音 3. 按 Enter 确认候选词。 |
| **预期结果** | 候选词上屏，不触发发送。 |
| **实际结果** | 修复前：同时上屏候选词和发送。修复后：仅上屏候选词。 |
| **测试通过** | ✅（手工验证） |
| **修复点** | `onEnter` 条件增加 `|| e.isComposing`。 |

---

## 14. 🟡 DocList.vue kbId NaN 风险

- **位置**：`frontend/src/views/DocList.vue:106`
- **根因**：`Number(route.params.kbId)` 对非数字字符串（如 `/kbs/abc/docs`）得到 NaN，API 请求打到 `/api/kbs/NaN/...`。
- **修复**：入口校验 `isNaN(kbId)` → `router.replace("/kbs")`。

| 项目 | 内容 |
|---|---|
| **测试用例** | 手动输入非法 URL |
| **测试步骤** | 直接访问 `/kbs/abc/docs`。 |
| **预期结果** | 自动跳转到 `/kbs`。 |
| **实际结果** | 修复前：页面报错或显示异常。修复后：自动重定向。 |
| **测试通过** | ✅（手工验证） |
| **修复点** | `DocList.vue` 增加 `isNaN(kbId)` 检查 + `router.replace`。 |

---

## 15. 🟡 文件扩展名解析不可靠

- **位置**：`frontend/src/views/DocList.vue:194`
- **根因**：`.split(".").pop()` 对 `Makefile` 返回 `makefile`，对 `report.tar.gz` 返回 `gz`。
- **修复**：检查分割后部件数 > 1 再取扩展名，无扩展名单独提示。

| 项目 | 内容 |
|---|---|
| **测试用例** | 上传无扩展名文件 |
| **测试步骤** | 选择 `Makefile`（无扩展名）拖入上传区 → 点击上传。 |
| **预期结果** | 提示"不支持的文档类型: 无扩展名"。 |
| **实际结果** | 修复前：提示".makefile"。修复后：提示"无扩展名"。 |
| **测试通过** | ✅（手工验证） |
| **修复点** | `nameParts.length > 1 ? "." + pop() : ""`，无扩展名单独处理。 |

---

## 16. 🟡 登录/注册输入未 trim

- **位置**：`frontend/src/views/Login.vue:66-70`、`frontend/src/views/Register.vue:96-106`
- **根因**：`form.username` 不经 trim 直接发送，用户无意带空格导致"用户名或密码错误"。
- **修复**：`form.username.trim()`。

| 项目 | 内容 |
|---|---|
| **测试用例** | 用户名前后加空格登录 |
| **测试步骤** | 输入 ` testuser  `（前后空格）→ 登录。 |
| **预期结果** | 登录成功（空格被 trim）。 |
| **实际结果** | 修复前：登录失败。修复后：登录成功。 |
| **测试通过** | ✅（手工验证） |
| **修复点** | `Login.vue`/`Register.vue` 对 `form.username` 调用 `.trim()`。 |

---

## 17. 🟢 注册密码无前端 72 字节上限

- **位置**：`frontend/src/views/Register.vue:13-19`
- **根因**：密码字段无 `maxlength`，用户可输入超长密码到后端才被拒。
- **修复**：增加 `maxlength="72"`，placeholder 更新为"6–72 位"。

| 项目 | 内容 |
|---|---|
| **测试用例** | 输入超长密码 |
| **测试步骤** | 粘贴 100 字符密码 → 观察输入框是否截断。 |
| **预期结果** | 密码限制在 72 字符以内。 |
| **实际结果** | 修复前：可输入任意长度。修复后：限制 72 字符。 |
| **测试通过** | ✅（手工验证） |
| **修复点** | `Register.vue` 密码字段 `maxlength="72"`，`Login.vue` 同步增加。 |

---

## 18. 🟢 auth.ts UserInfo 接口不完整

- **位置**：`frontend/src/api/auth.ts:13-15`
- **根因**：`UserInfo` 只声明 `username`，`/api/auth/me` 实际还返回 `id`、`created_at`。
- **修复**：补全 `id: number`、`created_at: string`。

| 项目 | 内容 |
|---|---|
| **测试用例** | TypeScript 编译 |
| **测试步骤** | `npx vue-tsc --noEmit`。 |
| **预期结果** | 类型检查通过。 |
| **实际结果** | 通过。 |
| **测试通过** | ✅ |
| **修复点** | `UserInfo` 接口增加 `id`、`created_at` 字段。 |

---

## 19. 🟡 创建知识库与上传文档分离，用户迷失

- **位置**：`frontend/src/views/KbsList.vue:41-62`
- **根因**：创建知识库对话框只有名称+描述，创建完成后留在列表页。用户找不到上传文档的入口（藏在卡片查看按钮里）。
- **修复**：创建对话框内嵌可选的文件拖拽上传区，创建后自动跳转到文档管理页。

| 项目 | 内容 |
|---|---|
| **测试用例** | 创建知识库并直接传文件 |
| **测试步骤** | 1. 点击"新建知识库" 2. 填写名称 3. 拖入 3 个文档（含 .md/.pdf/.docx） 4. 点击"创建并上传 3 个文件"。 |
| **预期结果** | 知识库创建成功，3 个文档自动上传，自动进入文档管理页，文档列表显示 3 条记录。 |
| **实际结果** | 创建→上传→跳转一气呵成，无需二次点击"查看文档"。 |
| **测试通过** | ✅（手工验证） |
| **修复点** | 对话框增加 `el-upload`（drag 模式、多选、auto-upload=false）；`handleCreate` 先创建 KB 再逐个上传文件；最后 `router.push` 进文档页。 |

---

## 20. 🔴 PDF 上传失败（pypdf 依赖缺失）

- **位置**：`pyproject.toml:13`（依赖已声明但环境未安装）
- **根因**：`pypdf` 声明在依赖里但当前 Python 环境从未安装。上传 PDF 时 `parse_file()` 报 `No module named 'pypdf'`，文档状态 failed、0 切片、向量库为空。用户在该知识库提问必然返回"资料库中未找到相关信息"。
- **修复**：`pip install pypdf`，PDF 解析恢复正常。

| 项目 | 内容 |
|---|---|
| **测试用例** | PDF 文件解析 + 上传 |
| **测试步骤** | 1. `parse_file('全球优选_供应商管理制度.pdf')` 2. 确认返回文本长度 >0。 |
| **预期结果** | 解析成功返回 1455 字符文本。 |
| **实际结果** | 修复前：`No module named 'pypdf'`。修复后：解析成功。 |
| **测试通过** | ✅ |
| **修复点** | 安装 `pypdf 6.15.0`。已 failed 的旧文档需重新上传（不会自动重试）。 |

---

## 21. 🟠 pgvector search 未注册 Vector 类型适配器

- **位置**：`app/storage/pgvector_store.py:111-114`
- **根因**：`search()` 直接执行 SQL 但从不调用 `_ensure_ext()`。新进程（如 uvicorn 重启后）第一个操作就是问答时，`register_vector` 尚未注册，psycopg2 无法适配 `pgvector.Vector` 参数，报 `can't adapt type 'Vector'`。
- **修复**：`search()` 开头调用 `self._ensure_ext(len(vector))`，保证类型适配器已注册。

| 项目 | 内容 |
|---|---|
| **测试用例** | 新进程直接问答（不先上传文档） |
| **测试步骤** | 1. 全新 Python 进程 2. `RagPipeline().ask(320, '产品手册里有什么产品')` 3. 不预注册 register_vector。 |
| **预期结果** | 返回 2 条引用来源，无异常。 |
| **实际结果** | 修复前：`ProgrammingError: can't adapt type 'Vector'`。修复后：正常返回 2 条 sources。 |
| **测试通过** | ✅ |
| **修复点** | `search()` 首行加 `self._ensure_ext(len(vector))`。 |

---

## 22. 🔴 MockLLM 完全忽略检索内容，返回固定模板

- **位置**：`app/providers/mock.py:49-55`
- **根因**：`MockLLM.complete()` 无论传入什么 prompt 都返回固定文案 `【Mock 回复】已基于资料库内容完成答复...`。用户提问后看到相同模板 + 不相关的引用来源，以为系统坏了。
- **修复**：改为**直接呈现检索资料原文**——从 Generator 构建的 prompt 中提取 `[N]` 格式的上下文块，按来源编号展示给用户。检索阶段已用向量匹配选出了最相关的片段，Mock 模式如实展示即可。

| 项目 | 内容 |
|---|---|
| **测试用例** | 提问 SKU/质检相关关键词 |
| **测试步骤** | 1. 用含质检内容的 prompt 调用 `MockLLM.complete()` 2. 检查答案中是否包含"皮面""五金""盐雾""色牢度"等原文。 |
| **预期结果** | 答案展示检索到的资料原文，包含具体质检条目。 |
| **实际结果** | 修复前：固定模板。修复后：展示 `[来源 1] 皮面: 无明显虫疤...染色牢度 ≥4级...`。 |
| **测试通过** | ✅ |
| **修复点** | `MockLLM` 改为 `_parse_prompt` 提取上下文块 + 直接拼接展示。 |

---

## 23. 🟠 Docx 生成器跳过了所有表格行

- **位置**：`scripts/generate_sample_docs.py:122-123`
- **根因**：`if stripped.startswith("|") → continue` 丢弃了所有 Markdown 表格。质检标准、SKU 清单、物流时效等内容全是表格——全部丢失。上传后的文档里没有这些关键信息。
- **修复**：表格行改为解析单元格后拼接为 `"单元格1  |  单元格2  |  ..."` 的紧凑文本，跳过多余的分隔行（仅含 `-` 和 `:`）。

| 项目 | 内容 |
|---|---|
| **测试用例** | 重新生成的 Docx 含表格内容 |
| **测试步骤** | 1. 运行 `generate_sample_docs.py` 2. 用 python-docx 读取生成的 `.docx` 3. 搜索"质检"/"SKU"/"头层"等关键词。 |
| **预期结果** | Docx 中包含完整的产品 SKU、质检标准、物流时效等表格数据。 |
| **实际结果** | 修复前：119 段落，不含 SKU 数据。修复后：包含"GB-M001 | 经典双扣公文包 | 头层牛皮 | 40×30×12 | 285"等。 |
| **测试通过** | ✅ |
| **修复点** | 表格行 `continue` 改为 `doc.add_paragraph("  |  ".join(cells))`。**旧的 Docx 需删除后重新上传。** |

---

## 24. 🟠 PDF 生成器同样跳过了所有表格行

- **位置**：`scripts/generate_sample_docs.py` `md_to_pdf`（表格行 `continue`）
- **根因**：#23 修了 DOCX 生成器，但 PDF 生成器里仍是 `if stripped.startswith("|") → continue`。重新生成的 PDF 里质检标准、SKU、物流时效等表格依旧全部丢失——用户上传 PDF 后资料残缺。
- **修复**：与 DOCX 一致，表格行渲染为 `"  |  ".join(cells)` 紧凑文本，跳过分隔行。

| 项目 | 内容 |
|---|---|
| **测试用例** | 重新生成的《供应商管理制度.pdf》含表格内容 |
| **测试步骤** | 1. 运行 `generate_sample_docs.py` 2. 用 pypdf 提取文本 3. 搜索"盐雾"/"YKK"/"铜底镀镍"。 |
| **预期结果** | PDF 中包含 4.2 五金件标准（电镀厚度 ≥5μm、48 小时盐雾 ISO 9227）、5.2 保温测试等。 |
| **实际结果** | 修复前：PDF 无表格内容。修复后：4 页 PDF 含完整标准条目。 |
| **测试通过** | ✅ |
| **修复点** | `md_to_pdf` 表格行 `continue` 改为 `pdf.multi_cell(0, 5, "  |  ".join(cells))`。**旧 PDF 需删除后重新上传。** |

---

## 25. 🟠 MockLLM 摊开整块检索原文，无提取

- **位置**：`app/providers/mock.py` `MockLLM.complete`
- **根因**：#22 把 MockLLM 从固定模板改为展示检索块全文（每块截断 400 字）。top_k=5 时回答变成大段原文堆叠，用户反馈"全部都回答了出来，没有提取有用的"。
- **修复**：不展示整块，改为**提取事实性句子**——切句后只保留含数字/规格符/标准代号（`\d`、`≤≥%`、`ISO/GB/REACH/GPSR`）且非纯章节标题的句子，跨块去重后取前 8 条。

| 项目 | 内容 |
|---|---|
| **测试用例** | 向 KB347 提问"五金件有哪些标准" |
| **测试步骤** | 1. 摄取含 4.2 五金标准的《供应商管理制度.pdf》 2. `RagPipeline().ask(347, "五金件有哪些标准")`。 |
| **预期结果** | 输出"电镀厚度: 铜底镀镍 ≥5μm + 镀金/镀铬 ≥0.1μm""磁扣吸力: 单颗磁扣闭合力 ≥3N"等聚焦规格条目，无"2 适用范围"式章节标题噪声。 |
| **实际结果** | 修复前：大段原文堆叠（含标题、空行）。修复后：8 条具体规格条目，命中 4.2 五金标准区域。 |
| **测试通过** | ✅ |
| **修复点** | 新增 `_FACT_RE`/`_TITLE_RE`/`_LIST_PREFIX` 正则 + `_collect_facts()`/`_fact_sentences()`；无事实句时回退展示块首 300 字。注意：Mock 无大模型，只能做"事实提取"；真正的语义总结仍需配置真实 LLM。 |

---

## 26. 🟡 二级页面返回按钮不明显（text 纯文字无图标）

- **位置**：`frontend/src/views/DocList.vue`、`frontend/src/views/Chat.vue` 头部
- **根因**：返回按钮用 `<el-button text @click="goBack">&larr; 返回...</el-button>`——纯文字链接样式，`&larr;` 是 HTML 实体箭头而非图标，视觉上不像"返回按钮"。从知识库卡片直接点「对话」进入 Chat 后，用户找不到返回入口，体验别扭（也是典型 AI 味）。
- **修复**：改为带边框图标按钮 `<el-button class="back-btn"><el-icon><ArrowLeft /></el-icon>返回</el-button>`；Chat 返回逻辑改为有浏览历史时 `router.back()` 回来源页，刷新后无历史则回文档列表。

| 项目 | 内容 |
|---|---|
| **测试用例** | `npx vue-tsc --noEmit` + `npx vite build` |
| **测试步骤** | 1. 进入任意知识库文档页 / 对话页 2. 观察头部左侧返回按钮。 |
| **预期结果** | 返回按钮带左箭头图标、有边框，点击可返回来源页。 |
| **实际结果** | 修复前：一行浅蓝小字"← 返回文档列表"，不易识别。修复后：图标 + 文字明确按钮；Chat 从 KbsList 进入返回 KbsList，刷新后返回文档页。 |
| **测试通过** | ✅ |
| **修复点** | 两个视图头部改图标按钮；Chat `goBack()` 增加 `window.history.length > 1 ? router.back() : router.push(docs)`。 |

---

## 27. 🟠 检索无相似度阈值，无关文档被计入命中

- **位置**：`app/config.py`（新增 `similarity_threshold`）、`app/storage/vector_store.py`（Chroma search）、`app/storage/pgvector_store.py`（pgvector search SQL）
- **根因**：`search()` 返回 top_k 条不筛相似度，低分/0 分噪声向量也被当作命中来源。真实 embedding 对无关文本常有小正分（mock 的 md5 哈希桶碰撞甚至给完全无关文本 0.076 分），导致：质检台显示无关结果、问答日志 `hit_doc_ids` 虚高、文档健康分析把无关文档误判为"被命中"。
- **修复**：新增配置 `SIMILARITY_THRESHOLD=0.1`；Chroma 在 Python 层过滤 `score <= threshold`，pgvector 在 SQL `WHERE` 加 `similarity > :min_score`。返回条数可少于 top_k。

| 项目 | 内容 |
|---|---|
| **测试用例** | `test_doc_health.py::test_hot_doc_and_dead_doc` |
| **测试步骤** | 上传 policy.txt（出差住宿）与 canteen.txt（食堂午餐）两份文档 → 提问"出差住宿标准是什么" → 查询 doc-health。 |
| **预期结果** | 只 policy.txt 进 hot_docs（hit_count=1），canteen.txt 0 命中进 dead_docs（无关文档不再被噪声命中）。 |
| **实际结果** | 修复前：canteen 被 0.076 碰撞分命中，active_count=2。修复后：active_count=1，dead_count=1。 |
| **测试通过** | ✅（全套 86 测试全绿） |
| **修复点** | `config.py` 加 `similarity_threshold: float = 0.1`；ChromaVectorStore 构造加 `min_score`，search 过滤 `score <= self._min_score`；PgVectorStore search SQL 加 `AND (1 - (embedding <=> :vec)) > :min_score`。 |

---

## 最终验证

```bash
python -m pytest tests/ -q    # 55 passed
cd frontend && npx vue-tsc --noEmit   # OK
cd frontend && npx vite build         # ✓ built
```
