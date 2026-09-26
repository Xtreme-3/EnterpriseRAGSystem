# K9 · 前端测试框架（vitest）

> 补齐 K8 明确留下的质量缺口。**动工前先写此卡片**（见 `AGENTS.md` 开发约定第 1 条）。

## 背景

K8 遗留项原文（`docs/bugfix-log.md`）：

> **前端无测试框架**（`package.json` 只有 `vue-tsc` + `vite build`，没有 vitest）。
> 本批前端改动**无法走 TDD**，只能靠类型检查 + 构建 + 手工复现。这是本批最大的质量缺口。

代价是可量化的：K8 改了 **20 处**错误解析传参、**8 个页面**的错误渲染、`Chat.vue` 的 abort 分流，
但 `bugfix-log.md` 里那些条目的「测试步骤」栏**全是手工步骤**，没有一条能被重跑。

而补上框架的**当天**，第一个新写的断言就照出一个从未生效的参数 —— 见 `bugfix-log.md` #40。

## 目标

`npm test` 可跑；覆盖纯函数 / store / 拦截器 / 路由守卫 / 组件五层；
`vue-tsc` 与 `vite build` 仍然通过；后端测试零回归。

## 依赖与版本约束（实测，非查文档）

| 包 | 落地版本 | 说明 |
|---|---|---|
| vitest | `^3.2.7` | **不是 latest**，理由见下 |
| @vue/test-utils | `^2.5.1` | |
| jsdom | `^30.1.1` | 环境 |

**为什么钉 vitest 3.x**（peer 实测）：

```
本仓已装 vite = 5.4.21
vitest@5.0.2 peerDependencies.vite = ^6.4.0 || ^7.0.0 || ^8.0.0   ✗
vitest@4.0.0 peerDependencies.vite = ^6.0.0 || ^7.0.0             ✗
vitest@3.2.7 peerDependencies.vite = ^5.0.0 || ^6.0.0 || ^7.0.0-0 ✅
vitest@2.1.9 peerDependencies.vite = ^5.0.0                        ✅
```

**不动 vite**：5 → 7 会连带 `@vitejs/plugin-vue` 5 → 6 与构建产物差异，
为测试框架付这个代价不值。代价是未来升 vite 时测试框架要一起升。

**为什么 jsdom 而不是 happy-dom**：10 个页面重度依赖 Element Plus，
它会用到 `document.createRange` / `getBoundingClientRect` / `ResizeObserver`，
jsdom 实现更全（Element Plus 自身的测试也是 jsdom）。~10 个文件规模下 happy-dom 的速度优势可忽略。

**不额外装的**：Pinia 用真 `createPinia()` + `setActivePinia()`，不装 `@pinia/testing`；
Element Plus 已在 `dependencies`，测试里挂 `global.plugins` 即可。

## 范围

1. `frontend/vite.config.ts` 加 `test` 块 —— **不新增 `vitest.config.ts`**：
   `tsconfig.node.json` 的 `include` 只有 `vite.config.ts`，新配置文件会跳出类型检查；
   而 `@` 别名只声明一次，测试与构建不会漂移。`defineConfig` 来源由 `vite` 改为 `vitest/config`（drop-in 兼容）。
2. 新增 `frontend/src/test/setup.ts`：`ResizeObserver` / `matchMedia` 兜底
   （jsdom 缺这两个时，Element Plus 组件挂载会抛 `TypeError`，表现为「这个页面的测试一律失败」）。
3. `frontend/package.json` 加 `test`（`vitest run`）与 `test:watch`（`vitest`）。
4. 五个 spec（co-located，与源文件同目录）：

| 文件 | 用例 | 覆盖 |
|---|---|---|
| `src/api/error.spec.ts` | 24 | `detailToMessage` 四种形态（字符串 / 422 数组 / 对象 / 缺失）、`safeParseJson`、`isAbortError`、`extractErrorMessage` 四条来源 + 4xx/5xx 分工 |
| `src/stores/auth.spec.ts` | 8 | `setAuth` 持久化、刷新恢复、`logout` 清干净、`refreshMe` 三态 |
| `src/api/client.spec.ts` | 6 | Bearer 注入、401 登出 + 跳转、`/auth/login` 自身 401 豁免、非 401 不登出、成功透传 |
| `src/router/index.spec.ts` | 5 | `requiresAuth` 拦截、子路由拦截、guest 反向跳转、放行、meta 标记 |
| `src/views/Login.spec.ts` | 8 | 真实挂载登录页：成功落态并跳转、四类失败文案、校验失败守卫、空表单不建登录态 |

## 验收

- [x] `npm test` —— **51 通过 / 0 失败**（5 个文件）
- [x] `npx vue-tsc --noEmit` —— exit 0
- [x] `npx vite build` —— 通过，且产物中不含任何 spec
- [x] 后端 `pytest tests/` —— 零失败（零回归）
- [x] **反向验证**（证明测试有牙齿）：把三个 K8 缺陷分别改回有 bug 的写法，
      共 **10 个用例**变红，且失败可归属：`detailToMessage` 不处理数组 → 6 个
      （4 个 `detailToMessage` + 1 个 `extractErrorMessage` + 1 个 `Login.vue`）；
      `isAbortError` 恒 false → 3 个；401 拦截器不登出 → 1 个

## 非范围（明确不做，记录决策避免被当成遗漏）

- **覆盖率门槛 / `@vitest/coverage-v8`**：先让测试存在，再谈门槛。
- **`Chat.vue` 的 SSE 全流程挂载测试**：要 mock `fetch` + `ReadableStream` + 流式帧时机，够单开一张卡。
- **升级 vite**（理由见上）。
- **接入 CI**：本仓库**没有 CI 配置**（`.github/workflows` 不存在），所以只进 npm scripts
  与 `AGENTS.md`/`README.md` 的完成定义，与后端 `pytest` 同级。
- **不加 `@pinia/testing`、不加 happy-dom**。

## 两个必须处理的坑（本轮专门查出来的）

1. **`npm run build` 会连带检查测试文件** —— `tsconfig.json` 的 `include` 是 `src/**/*.ts`，
   而 `build` 脚本是 `vue-tsc && vite build`，因此放在 `src/` 下的 `*.spec.ts` 会被 `vue-tsc` 检查。
   → 一律**显式** `import { describe, it, expect, vi } from "vitest"`，**不开 globals**，
   这样不用往 tsconfig 的 `types` 里塞 `vitest/globals`，`vue-tsc` 照样过。
   （已按此实现，`vue-tsc` exit 0。）
2. **不会打进产物**：`vite build` 只从 `index.html` 的可达图打包，孤立的 `*.spec.ts` 不进 bundle。

## 一个已查清的 jsdom 差异（**不是缺陷，不改业务代码**）

> 这篇的初版曾写成「未定论观察 —— 未在真实浏览器复现前不定性」。
> 那个处理方式被用户指出是**回避**：既然读写到的行为与库源码逻辑相悖，就该查到底，
> 而不是挂成「观察」。已按 `systematic-debugging` 走完根因调查 + **真实 Chromium 复现**，
> 结论如下（**初版结论已被真实浏览器验证推翻**）。

环境：Element Plus **2.14.3**（`package.json` 声明的是 `^2.5.0`，`^` 允许装到 2.14.3）+ jsdom。

### 在 jsdom 里观察到的现象

空表单点「登录」时：

- 每个字段的 `validate("")` **都会 reject**（已单独验证 `fields[0].validate("")` → rejected）；
- 但 **`ElForm` 级别的 `validate()` 会 resolve `true`**，于是请求带着空用户名/密码发了出去。

### 根因（已锁定，带证据）

`ElForm.validate()` → `validateField(void 0)` → `doValidateField([])` →
`filterFields(fields, [])` 返回全部 2 个字段（`utils.mjs:35-38`）→ 逐个
`await field.validate("")`。到这里都正常。断点在**字段抛出的那个值**：

```
ElFormItem.validate() 内部 doValidate(rules) 失败
  → catch 里 `const { fields } = err`        ← err.fields 为 undefined
  → return Promise.reject(undefined)          ← 失败信号丢在这里
表单侧 catch (fields) 收到 undefined
  → validationErrors = { ...validationErrors, ...undefined }   ← {...undefined} 合法 → {}
  → Object.keys({}).length === 0 → return true                ← 失败被判成通过
```

「矛盾」的实质不是逻辑矛盾，而是 **`ElFormItem.validate()` reject 了一个 `undefined`**，
让表单的 `{...undefined}` 累加器得到空对象、把失败误判成成功。

支撑证据（逐条实测）：`fields.length = 2` 且类型是 `function`；包一层计数器确认循环**确实**调了这 2 个字段；
每个字段单独调都 reject；但 `validateField([])` / `validate()` 均 `RESOLVED true`；
`validateState` 卡在 `username:validating | password:validating`（成功/失败分支都没走到）；
**rejection 的值是 `undefined`**（`typeof e === "undefined"`、`Object.keys(e)` 为空、`String(e) === "undefined"`）；
而单独复刻 `doValidate` 时 `async-validator` 本身正常返回带 `fields` 的 `AsyncValidationError`，
库自己的告警（`onValidationFailed`）也一次没打 —— 即**真实路径里到达 catch 的 `err` 不是同一个对象**。

### 真实浏览器验证（决定性）

本机已有 playwright 的 chromium 缓存，于是**不是靠推断，而是真的开了浏览器**：
dev server `localhost:5173` + 真实 Chromium + Element Plus 2.14.3 + 生产代码，
钩住 XHR 后点空表单「登录」：

```
reqs          = []                            ← 一个请求都没发
inline-errors = ["请输入用户名","请输入密码"]   ← Element Plus 内联错误正常显示
toasts        = []
url           = /login
```

**与 jsdom 下的行为完全相反。** 即：

- **生产代码没有任何问题** —— 空表单被正常拦住、零请求、两条内联错误、停在登录页；
  业务代码一行都不用改。
- 这个现象**只存在于 vitest + jsdom 环境**，是**测试环境的差异（测试假象）**，不是库缺陷也不是本仓缺陷。
- 但它**确实是我原套件里一条失败测试的真实成因**，所以「需要处理」是对的 —— **要处理的是测试**。

### 处置（已完成）

1. 把「校验拦住表单」这类**断言第三方内部行为**的用例从套件移除 —— 它脆弱，且测的不是本仓代码。
2. 改为两条只断言**本仓契约**的用例：①用「校验必然失败」的 `el-form` 替身，确定性验证 `Login.vue`
   自己的守卫分支（`valid === false` 时直接 return，不发请求不弹提示）；②不假设校验结果，
   只断言「空表单点了也不会建立登录态」。

### 留给后人的一句话

> 写 Element Plus 组件的测试时，**不要断言「表单校验会拦住提交」这类结果**——
> 它在 jsdom 下不一定成立（本仓实测 `ElFormItem.validate()` 会 reject `undefined`，
> 使表单把它当成通过）。要验这个分支，就用「校验必然失败」的替身组件去驱动，
> 断言的是**本仓代码对结果的反应**，而不是库内部怎么判。

## 参考

- `docs/bugfix-log.md` #40（本轮由新测试直接照出的缺陷）
- `docs/bugfix-log.md` #36 / #37 / #35（本轮补齐回归测试的三个 K8 缺陷）
