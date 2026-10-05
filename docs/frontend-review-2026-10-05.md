# 前端界面评审 · 2026-10-05

> **这份文件是什么**：对 4 张截图（chat / inspect / kbs / diagnostics）的界面评审 + 可直接粘贴给 AI 的修改提示词。
> 它**不是**设计规范 —— 设计规范的唯一来源仍是 `DESIGN.md`。评审结论采纳后，规范该改的地方改回 `DESIGN.md`。

---

## 一、总评

**评级：B（良好，方向对，执行有残留）。**

这套东西的质量明显高于一般 AI 产物。`DESIGN.md` 里那些约束（零阴影、单一彩色占比 <10%、标题衬线、圆角分层、不套后台模板）
不是抄来的套话，是**针对这个产品形态做过的判断** —— 尤其"10 个页面里只有 2 个是表格页，核心是对话页"这句，
说明规范是先盘点自己有什么、再定规则，而不是先找参照物往头上套。这一层做对了。

问题出在**执行层**，而且集中在两类：

1. **规范自己立了、代码没跟** —— `DESIGN.md` 写了零阴影、不写死色值、只用 16/8 间距，代码里三条都在被违反。
2. **页面信息密度倒挂** —— 诊断页"一切正常"时占用 600px 显示两个巨大绿勾，出问题时反而信息更紧凑；
   质检页把 6px 高、占满整行的相似度进度条当成主角，真正要判断的切片原文被压成 4 行灰字。

一句话：**规范像 A，执行像 B-，其中 Diagnostics 一页最像 C。**

---

## 二、已经做对的，不要推翻

- 卡片"白底 + 1px #e4e7ed 边框 + 8px 圆角 + 零阴影"这套分层语言，全站一致，是对的。
- 品牌墨绿 #0f6e56 覆盖 Element Plus 变量、圆角分层变量（`--radius-container/card/button`）、
  标题衬线 / 正文无衬线的双字体系 —— 这三样是全站"像同一个产品"的来源。
- `StatCard.vue` 从两个看板页抽公共组件，`ChatConversations.vue` 桌面侧边栏与窄屏抽屉复用同一份 ——
  这两个抽象做得干净，别再合回去。

---

## 三、问题清单

### P0-1 · Diagnostics 健康态：两个巨大居中绿勾 + 600px 空白

**证据**：`Diagnostics.vue` L62-67（摄取健康）与 L90-103（向量一致性）都用 `el-result`。
`el-result` 自带约 40px 上下内边距 + 大号图标 + 居中排版，单块约 300px 高，两块就是 600px。

**为什么是错的**：

- **信息密度倒挂**。有失败文档时，页面渲染的是 `fail-group` 卡片（紧凑、有信息量）；
  一切正常时反而渲染两个巨型空块。健康态本来就该"一句话带过、让用户赶紧走"，现在成了页面主体。
- **同级结论排版不一致**：「摄取健康」没有 section 标题，「向量层与元数据一致」有 `h3` 标题，且标题左对齐、内容居中 —— 两种对齐基准在同一个页面打架。
- 截图里 960px 宽、约 700px 高的内容区，实际有效信息只有 6 个数字和一个绿勾，其余全是空的。

**改法**：把"一切正常"压成**横向状态条**（高 40–48px，左侧一枚 16px 图标 + 一行文字 + 右侧辅助数字），
两条状态条纵向排列，紧贴统计卡下方。`el-result` 只留给**真正的空态和错误态**（无文档、向量库不可达、加载失败）。

### P0-2 · KbsList 卡片操作区：8 个按钮挤成错位的两行

**证据**：`KbsList.vue` L28-46 共 8 个按钮（1 primary + 6 text + 1 danger），
外层 `.kb-card-actions { display:flex; justify-content: space-between }`（L491-498），
内层 `.kb-action-group { flex-wrap: wrap; gap: 0 }`（L500-504）。

**为什么是错的**：

- 320–340px 宽的卡片装 8 个按钮必然折行；折行后 group 内部行宽不齐，
  而 `space-between` 又把「删除」推到最右侧单独成列 —— 截图里就是
  「进入对话｜文档 质检｜删除」/「体检 健康 / 历史 成员」这种参差。
- **更根本的是信息架构**：卡片上一口气暴露 8 个入口，而且「质检 / 体检 / 健康」三个中文词在用户眼里几乎同义
  （实际分别是检索效果评测、索引健康度、文档级状态）。用户记不住，等于没有入口。

**改法**：卡片只留「进入对话」（主）+「文档」（次），其余收进一个「更多 ⌄」下拉。
「删除」放进下拉最后一项，用 `type="danger"` 的**文字**按钮，`divided` 分隔。
另：三个诊断页的中文名应改为「检索测试 / 索引体检 / 文档状态」，让名字自带区分度。

### P0-3 · InspectBench 的 top_k 控件是坏的，且与 Chat 页同一个参数两个形态

**证据**：

- `InspectBench.vue` L31-40：`el-slider` 同时带 `show-input` + `size="small"` + `style="width:180px"`。
  截图里滑轨被压得只剩左边一个圆点 + 右边一个数字框，滑块本体几乎不可见、不可拖。
- `Chat.vue` L41-52 同一个参数叫「条数」，用 `el-input-number`；
  `InspectBench.vue` 叫「top_k」，用 `el-slider`。**一个概念、两种控件、两套标签、一中一英。**

**改法**：统一为 `el-input-number`（`:min="1" :max="20"`），标签统一中文「检索条数」，宽度 ≥100px，不要 `show-input`。
质检页顶部只留 Query 输入（large）+ 模式切换 + 检索按钮，模型 / 条数 / 重排折进「高级参数」折叠区。

### P0-4 · Chat 头部一行塞 6 个控件，其中一个是永久禁用

**证据**：`Chat.vue` L17-64：返回按钮 + 知识库名 + 2 段 radio + 模型 select + 条数 input-number + 重排 switch + 常驻提示文案。

**为什么是错的**：

- 「重排」在本项目后端 `RERANK=false`，`rerankAvailable` 恒为 false，所以这个开关**永远是灰的**。
  主界面上摆一个永远不可用的控件，用户的第一反应是"这界面坏了"。要么不渲染它，要么渲染成一行说明文字。
- 「Enter 发送，Shift+Enter 换行」常驻最右侧 —— 用过一次就再也不看的噪音，挤掉了真正的内容宽度。
- 检索参数（模式 / 模型 / 条数 / 重排）是开发者调参，和「这是哪个知识库」不该同级。

**改法**：头部只留 `返回` + `知识库名`，最右侧一个「参数 ⚙」图标按钮；
参数全部收进点开的 popover（内部纵向排布，每个参数一行）。重排不可用时**不渲染开关**。
输入提示改为在输入框获得焦点时才出现的 placeholder 或 footer 微文案。

### P1-5 · 硬编码色值回潮（规范明文禁止）

`DESIGN.md` L186-187 写明"新代码直接用 `--el-color-primary*` 变量，不要写死十六进制"。
实际散落着大量字面色值，其中 `#f0f0f0`（`InspectBench.vue` L304 分数条底）和
`#f2f2f2`（`KbsList.vue` L493 操作区分割线）**连色板里都不存在**：

| 字面值 | 出现位置 | 应有变量 |
|---|---|---|
| `#e4e7ed` | KbsList / Chat / Diagnostics / DefaultLayout 多处 | `--app-hairline` |
| `#909399` | 同上 | `--app-ink-muted` |
| `#303133` / `#606266` / `#c0c4cc` | 同上 | `--app-ink` / `-secondary` / `-disabled` |
| `#fafafa` | `Chat.vue` L821 | `--app-surface-sunken` |
| `#f0f0f0` / `#f2f2f2` / `#404040` | InspectBench / KbsList | **色板外，需新增或替换** |

**改法**：在 `style.css` 把 `DESIGN.md` 第二节的色彩**全部**注册为 `--app-*` 变量，各页只引用变量。
色板外的三个值要么替换成已有色，要么补进 `DESIGN.md`（更可能是后者：分数条底应该是 `--app-hairline-soft` #ebeef5）。

### P1-6 · 字号与间距超出规范，且"例外"正在扩散

- **13px 已经变成事实上的第五级**：`KbsList.vue` L474（卡片描述）、`Chat.vue` L778/L826（失败提示、切片原文）、
  `Diagnostics.vue` L263（失败组标题）、`InspectBench.vue` 多处。
  `DESIGN.md` L244 只允许"表格内正文 13px"这一条例外，现在已经扩散到卡片、提示、正文块。
- **间距混用**：`Chat.vue` L842 `gap:12px`、`Diagnostics.vue` L232 `gap:12px` / L212 `margin-bottom:20px`、
  `KbsList.vue` L434 `padding: 20px 20px 12px`。规范说"新代码一律 16/8"，但这些都是注释里标着 P0 的新代码。

**改法**：13px 二选一 —— ① 只保留在 `<table>` 单元格里，其余改回 12 或 14；
② 承认它是第五级「次级正文」，写进 `DESIGN.md` 并限定使用范围。**不要放着不管**，
因为 `DESIGN.md` 自己反对"差 1–2px 的伪层级"。间距统一收敛到 16/8，20/24 只留给页面级分隔。

### P1-7 · 语义色被当成装饰色用（3 处）

- `KbsList.vue` L170-174 `ROLE_TAG` 把 `owner` 映射成 `warning`（橙色）。
  `DESIGN.md` L210 定义 warning = "处理中、告警"。"所有者"是权限角色，不是异常状态 ——
  橙色 tag 会让用户以为这张卡片有问题。改为 owner 用品牌色 plain tag，editor / viewer 用 `info` / 无底色。
- `InspectBench.vue` L71 `<el-tag type="info">{{ hit.filename }}</el-tag>` ——
  用状态色 tag 承载文件名这种纯标识信息，一屏 5 个结果就是 5 个灰底块，纯噪音。改成普通文本 + `ink-muted`。
- `DefaultLayout.vue` L10 `<el-tag type="danger" effect="dark">管理员</el-tag>` ——
  **实心红块**做角色徽章，既是语义色误用，也违反"彩色面积 <10%"。改成 `plain` 或品牌色描边 tag。

### P1-8 · InspectBench 结果卡信息层级倒置，且违反零阴影

- 截图里每张命中卡最抢眼的是那条**占满整行、品牌绿、6px 高**的相似度进度条（L75-81），
  而真正要判断的切片原文被 `-webkit-line-clamp: 4` 压成 4 行 13px 灰字（L330-339）。
  质检台的目的是判断"这个切片对不对"，内容才是主体，分数只需要一个数字。
- 卡片 padding 16px、内容只有两行，却有 150px+ 高度，「展开全部」一个 link 按钮孤零零居中偏左。
- `L248-250` `:hover { box-shadow: 0 2px 8px rgba(0,0,0,.06) }` —— **直接违反 `DESIGN.md` L149「全站不使用 box-shadow 做立体感」**，
  且同页规范要求 hover 只变边框色。

**改法**：分数条固定宽度 80–120px（不要 `flex: 1`），或直接换成带色阶的数值 tag（≥90% 绿 / 70–90 默认 / <70 橙）；
把省下的空间给切片正文，`line-clamp` 提到 6–8 行；hover 改 `border-color`。

### P2-9 · Chat 欢迎态是"模板 hero"形态

`Chat.vue` L685-699：居中 `h3` + 两行说明 + 更浅的副文案，正是 `DESIGN.md` L427 Don'ts 点名的
"堆叠居中标题 + 副标题 + 描述"。建议改成左对齐、并直接给 2–3 个可点击的示例问题（空态的价值在于引导，不在于抒情）。

### P2-10 · KbsList 只有 2 张卡时右侧 500px 全空

`grid-template-columns: repeat(auto-fill, minmax(320px, 1fr))`（L416）在 1200px 画布 + 2 张卡时只占 320px。
这是 `auto-fill` 的正常行为，但观感像"页面没加载完"。可选做法：卡片数量 < 3 时给网格加 `max-width` 让它居中，
或补一个「最近活动 / 最近提问」侧栏填满右侧。属于可选优化，不急。

### P2-11 · `DESIGN.md` 自身 3 处与代码不符

规范失效比单页丑更危险 —— 下次 AI 读规范会照着错的那版改。

| 位置 | `DESIGN.md` 写的 | 代码实际 |
|---|---|---|
| L356 | 用户气泡 = `{colors.brand}` 底 + 白字 | `Chat.vue` L727-730 用 `--el-color-primary-light-9`（#e7f1ee）+ 深字 |
| L243-244 | 字号只有 20/15/14/12 四级 | 13px 已扩散为第五级（见 P1-6） |
| L369-373 | 引用芯片"白底 + 1px 边框"，文件名 `ink`、相似度 `ink-muted` | `Chat.vue` L797-806 整枚芯片统一 `#303133`，没做两级区分 |

**改法**：把代码改成规范，或把规范改成代码 —— 二选一，但必须同步。建议用户气泡按规范改（品牌绿实底更能区分说话人）。

---

## 四、给 AI 的提示词（可直接复制）

> 用法：一次只贴一条，改完跑 `npm run build` 并肉眼看一遍页面再进下一条。
> 每条都限定在 `DESIGN.md` 既有规则内，不引入新依赖、不引 web font。

### 提示词 1 —— Diagnostics 健康态重构（P0-1）

```
读 DESIGN.md，然后只改 frontend/src/views/Diagnostics.vue，不要动其他文件。

现状问题：L62-67 和 L90-103 用 el-result 渲染「摄取健康」和「向量层与元数据一致」。
el-result 自带大图标 + 居中 + 约 40px 上下内边距，两个正常态结论就吃掉约 600px 高度，
在 960px 宽的页面里 90% 是空白。健康态的信息密度比异常态还低，这是倒挂。

要求：
1. 新增一个局部子组件（可直接写在本文件的 <script setup> 里或用内联模板）叫状态条：
   横向 40-48px 高，白底 + 1px var(--el-border-color-light) + var(--radius-card) 圆角，
   左侧 16px 语义色图标（成功=var(--el-color-success) / 警告=warning / 危险=danger），
   中间一行 14px var(--el-text-color-primary) 主文案，
   右侧 12px var(--el-text-color-secondary) 辅助数字（如「4 篇 · 63 切片」）。
2. 「摄取健康」和「向量一致性」都改成这种状态条，纵向 gap 16px，紧贴 stat-grid 下方。
   两条都带各自的 15px/600 区块标题（保持同级排版一致），标题左对齐，状态条左对齐 —— 不要居中。
3. el-result 只保留给真正的空态/错误态：无文档、向量库不可达、加载失败。这三处不要改。
4. 向量库不可达时状态条用 warning 色，文案「向量库不可达 · 无法对比，请检查向量库服务」。
5. 失败文档存在时，保留现有的 fail-group 卡片不动（那一块本身是对的）。
6. 间距只用 16 和 8 两档；不要新增色值，一律用 --el-* 变量或 style.css 里已有的 --fs-*/--radius-*。

改完自检：整页在「一切正常」时应该只有统计卡网格 + 两条 48px 状态条，总高不超过 260px。
```

### 提示词 2 —— KbsList 卡片操作区收敛（P0-2）

```
读 DESIGN.md，然后只改 frontend/src/views/KbsList.vue。

现状问题：L28-46 一张卡片上有 8 个按钮（进入对话 + 文档/质检/体检/健康/历史/成员 + 删除）。
.kb-card-actions 用 justify-content: space-between，.kb-action-group 用 flex-wrap: wrap，
在 320-340px 宽的卡片里必然折行且对齐参差 —— 截图里是「进入对话｜文档 质检｜删除」/
「体检 健康 / 历史 成员」这种错位。DESIGN.md 的反模式一节明确要求「一个主操作 + 其余全部灰色文字按钮」。

要求：
1. 卡片底部只留两个：左侧 el-button type="primary"「进入对话」，
   紧邻一个 el-button text「文档」。
2. 其余全部收进一个 el-dropdown，trigger 是一枚 el-button text「更多 ⌄」：
   下拉项依次为 检索测试 / 索引体检 / 文档状态 / 问答历史 / 成员管理（仅 owner）。
   在「成员管理」之前加 divided 分隔，最后一项「删除知识库」用
   el-dropdown-item 的 divided + 危险色文字（class 上给 color: var(--el-color-danger)），
   点击后仍走原来的 el-popconfirm 二次确认逻辑，不要直接删。
3. 把 goInspect / goDiagnostics / goDocHealth / goQaLogs 的路由参数保持原样，只改入口形态。
4. 顺手修 L170-174：ROLE_TAG 里 owner 现在是 "warning"（橙色）——
   DESIGN.md 定义 warning = 处理中/告警，用它表示权限角色是语义色误用。
   owner 改为 "primary" 且 effect="plain"，editor 改为 "info"，viewer 也 "info" 但 effect="plain"。
   如果 el-tag 的 primary 需要 el-tag type 支持，就用自定义 class 让它描边用 var(--el-color-primary)。
5. .kb-card-actions 改为 display:flex; justify-content: flex-start; gap: 8px;
   删掉 space-between（两个按钮不需要撑满），.kb-action-group 整个删掉。
6. L474 .kb-desc 的 13px 改成 12px（DESIGN.md 四级字号里没有 13px）。
7. L493 的 border-top: 1px solid #f2f2f2 改成 var(--el-border-color-lighter)（#f2f2f2 不在色板里）。

不要动卡片本体的白底 / 边框 / 圆角，那部分是对的。
```

### 提示词 3 —— InspectBench 参数栏与结果卡（P0-3 + P1-8）

```
读 DESIGN.md，然后只改 frontend/src/views/InspectBench.vue。

现状有三个问题：

问题一（参数控件坏了 + 与对话页不统一）：
L31-40 的 top_k 用 el-slider 同时带 show-input / size="small" / width:180px，
实际渲染出来滑轨几乎不可见、只剩左边一个圆点和右边一个数字框，不可用。
而 Chat.vue L41-52 同一个参数叫「条数」、用 el-input-number。
同一个概念两种控件、两套标签（一中一英）是不允许的。

问题二（结果卡信息层级倒置）：
L75-81 的相似度进度条占满整行、品牌绿、6px 高，是全卡最抢眼的元素；
而真正要判断的切片原文被 L336-338 的 -webkit-line-clamp: 4 压成 4 行 13px 灰字。
质检台的目的是判断「这个切片对不对」，内容才是主体。

问题三（违反零阴影）：
L248-250 .hit-card:hover 用了 box-shadow: 0 2px 8px rgba(0,0,0,.06)，
DESIGN.md 明确「全站不使用 box-shadow 做立体感」，同页规范要求 hover 只变边框色。

要求：
1. top_k 换成 el-input-number（:min="1" :max="20" :step="1"，不要 controls-position 靠右以外的花活），
   标签文字统一成「检索条数」，去掉英文 top_k。
2. 质检页顶部只保留三样：Query 输入（size="large"）、检索模式 el-radio-group、主按钮「检索」。
   模型 / 检索条数 / 重排 折进一个「高级参数」折叠区（el-collapse 或 el-popover），默认收起。
3. 分数条固定宽度 120px（去掉 flex: 1），数字右对齐；或者直接换成带色阶的数值 tag：
   ≥90% 用 var(--el-color-success)、70-90% 用 var(--el-text-color-primary)、<70% 用 var(--el-color-warning)。
   二选一，我倾向后者 —— 一个数字就够，进度条没有增量信息。
4. .hit-content 的 -webkit-line-clamp 从 4 提到 8，字号用 14px（DESIGN.md 四级里的正文级），
   行高 1.7，颜色 var(--el-text-color-regular)。把省下的横向空间全部给正文。
5. .hit-card:hover 改成 border-color: var(--el-color-primary-light-5)，删掉 box-shadow。
6. L71 的 <el-tag type="info">{{ hit.filename }}</el-tag> 改成普通 <span>，
   文件名用 var(--el-text-color-primary) 加 500 字重，chunk 编号保持 var(--el-text-color-disabled)，不用 tag。
7. 「展开全部」按钮：L336-338 的截断逻辑保留，但按钮改成右对齐（align-self: flex-end），
   文案在折叠时用「展开全部 · 共 N 字」，展开后用「收起」。
8. 全文件把字面色值 #e4e7ed / #909399 / #606266 / #c0c4cc / #404040 / #f0f0f0
   替换成 --el-border-color-light / --el-text-color-secondary / --el-text-color-regular /
   --el-text-color-disabled / --el-text-color-primary / --el-border-color-lighter。
   #f0f0f0 分数条底用 --el-border-color-lighter。

改完自检：单张命中卡的正文可见字数应该明显增加，同时卡片总高不超过现在。
```

### 提示词 4 —— Chat 头部参数收纳（P0-4）

```
读 DESIGN.md，然后只改 frontend/src/views/Chat.vue 的 .chat-header 区域（L17-64 及其样式）。

现状问题：头部一行塞了 6 个元素 —— 返回按钮、知识库名、混合/纯向量 radio、
模型 select、条数 el-input-number、重排 el-switch、以及最右侧常驻的「Enter 发送，Shift+Enter 换行」。
其中「重排」在本项目后端 RERANK=false 时 rerankAvailable 恒为 false，开关永远置灰 ——
主界面上有一个永远不可用的控件，用户会认为界面坏了。

要求：
1. 头部只保留三样：el-button「返回」（左侧）、知识库名（16px/600 衬线，保持现状）、
   最右侧一枚 el-button text + Setting 图标的「参数」按钮（加 aria-label="检索参数"）。
   删掉常驻的「Enter 发送…」提示 —— 改为输入框获得焦点时才显示的 placeholder 后缀或输入框下方 12px 灰字。
2. 点「参数」弹 el-popover（width 280px，纵向排布，每个参数一行：左侧 12px 灰字标签 + 右侧控件）：
   - 检索模式：el-radio-group（混合 / 纯向量）
   - 模型：el-select（仅当 modelChoices.length > 1 时渲染）
   - 检索条数：el-input-number（:min="1" :max="10"）
   - 重排：rerankAvailable 为 false 时**不渲染开关**，改渲染一行 12px var(--el-text-color-disabled)
     文案「重排未启用（服务端 RERANK=false）」
3. 逻辑部分（mode / model / topK / rerank / rerankAvailable 的 ref 与 loadChatOptions）完全不动，
   只改这些控件在模板里的位置和容器。
4. 顺手修 L727-730：DESIGN.md L356 规定用户气泡是 {colors.brand} 底 + 白字，
   代码现在用的是 --el-color-primary-light-9（#e7f1ee）+ 深色字。按规范改成
   background: var(--el-color-primary); color: #fff;，保持 8px 8px 2px 8px 圆角不变。
5. 顺手修 L842 .chat-input-area 的 gap: 12px → 8px（间距只用 16/8）。
6. 顺手修 L685-699 .chat-welcome：「居中 h3 + 两行说明」是 DESIGN.md Don'ts 里点名的模板 hero 形态。
   改成左对齐、顶部对齐（margin-top 从 80px 降到 24px），
   并在说明下方加三个可点击的示例问题 chip（用 .chat-reason-chip 的样式，点击即填入输入框），
   示例问题就写「供应商准入需要满足哪些条件？」这类跟知识库内容相关的。

界面宽度紧张时要保证知识库名不被挤压（min-width: 0 + ellipsis），返回按钮不被换行。
```

### 提示词 5 —— 全站色值 / 字号 / 间距 token 收敛（P1-5 + P1-6）

```
读 DESIGN.md 第二节（色彩）和第四节（布局），然后改 frontend/src/style.css
以及 KbsList.vue / Chat.vue / Diagnostics.vue / InspectBench.vue / DocList.vue / DocHealth.vue / QaLogs.vue
的 <style scoped> 块。这个任务只做「字面值 → 变量」的替换，不改任何布局结构和组件形态。

第一步，在 style.css 的 :root 里补齐 DESIGN.md 第二节列出的全部色值变量（命名与 DESIGN.md 对齐）：
  --app-canvas / --app-surface / --app-surface-sunken
  --app-hairline / --app-hairline-strong / --app-hairline-soft
  --app-ink / --app-ink-secondary / --app-ink-muted / --app-ink-disabled
  --app-brand / --app-brand-hover / --app-brand-soft / --app-brand-border / --app-brand-line
另外补两组刻度（DESIGN.md §四 §三要求，现在缺）：
  --app-gap / --app-gap-sm（16 / 8）
  --radius-item: 6px（列表条目用，现在代码里散写 6px）
注意：#f0f0f0 和 #f2f2f2 不在 DESIGN.md 色板里，不要为它们新建变量 ——
前者（InspectBench 分数条底）换成 --app-hairline-soft，后者（KbsList 操作区分割线）换成 --app-hairline-soft。

第二步，逐文件把字面色值替换成变量：
  #f5f7fa → var(--app-canvas)      #ffffff / #fff → var(--app-surface)
  #fafafa → var(--app-surface-sunken)
  #e4e7ed → var(--app-hairline)    #dcdfe6 → var(--app-hairline-strong)   #ebeef5 → var(--app-hairline-soft)
  #303133 → var(--app-ink)         #606266 → var(--app-ink-secondary)
  #909399 → var(--app-ink-muted)   #c0c4cc → var(--app-ink-disabled)
  #f56c6c → var(--el-color-danger)   #67c23a → var(--el-color-success)   #e6a23c → var(--el-color-warning)
  #0f6e56 / #87b7ab / #e7f1ee / #b7d4cc → var(--app-brand*) 系列
替换范围包括 <style scoped> 里的色值，以及模板里内联 style="color:#909399" 这类写法（KbsList.vue L74 有一处）。

第三步，字号收敛：全站只允许 20 / 15 / 14 / 12 四级（变量 --fs-page / --fs-card / --fs-body / --fs-hint，
已在 style.css 定义）。所有 13px 按位置处理：
  - 出现在 <el-table> 单元格相关的样式里 → 保留 13px 并把值写成 var(--fs-table)，同时在 style.css 里
    追加 --fs-table: 13px，并在注释里标明「仅限表格单元格，见 DESIGN.md §三例外」。
  - 出现在卡片描述、失败提示、切片原文、区块标题等非表格位置 → 改回 var(--fs-body) 或 var(--fs-hint)。

第四步，间距收敛：所有 margin / padding / gap 里的 12px / 20px 改到 16px 或 8px
（页面级大分隔 24px 保留）。已知需改的：
  Chat.vue .chat-input-area gap:12px → 8px、.chat-header padding-bottom:12px → 16px
  Diagnostics.vue .stat-grid gap:12px → 8px、.diag-header margin-bottom:20px → 24px、
    .fail-group padding: 12px 16px → 16px
  InspectBench.vue .query-area gap:12px / .hits-list gap:12px / .hit-card gap:12px → 16px 或 8px
  KbsList.vue .kb-card-body padding: 20px 20px 12px → 16px

做完给我一份替换清单（文件 → 替换了几处 → 剩几处待人工确认），
不要顺手改布局结构，这一步只做 token 化。
```

### 提示词 6 —— 语义色滥用清理（P1-7）

```
读 DESIGN.md 第二节「语义（仅用于状态）」和第八节 Don'ts，然后修三处语义色误用。
语义色的合法用途只有：状态 Tag、分数条、统计卡数字、错误提示文字。

1. frontend/src/layouts/DefaultLayout.vue L10
   现在：<el-tag v-if="authStore.isAdmin" type="danger" size="small" effect="dark">管理员</el-tag>
   问题：用 danger（红）表示「管理员」权限，既是语义误用，实心红块也是一个高饱和色块
   （DESIGN.md 要求彩色面积 <10%）。
   改成：品牌色描边 tag，effect="plain"，文案不变。

2. frontend/src/views/KbsList.vue L170-174 ROLE_TAG
   owner 现在是 "warning"（橙）。warning 的语义是「处理中 / 告警」，权限角色不是告警。
   改成：owner = 品牌色 plain tag（描边 var(--el-color-primary)，文字 var(--el-color-primary)）、
   editor = info、viewer = info plane。三个角色都不应该用 success / warning / danger。

3. frontend/src/views/InspectBench.vue L71
   现在：<el-tag size="small" type="info">{{ hit.filename }}</el-tag>
   问题：文件名是纯标识信息，不是状态；一屏 5 个结果就有 5 个灰底 tag，纯视觉噪音。
   改成：普通 span，字体 13px、字重 500、颜色 var(--el-text-color-primary)；
   紧邻的 chunk 编号 span 用 var(--el-text-color-disabled)。

改完统计一下全站还有哪些 el-tag 的 type 是以「分类」而不是「状态」为目的在用的，列给我，不要自己顺手改。
```

### 提示词 7 —— 让 DESIGN.md 与代码重新对齐（P2-11）

```
读 DESIGN.md 和 frontend/src，核对 DESIGN.md 里描述与代码实际是否一致。
已知 3 处不一致，请先确认，再按我给的方向修「代码」那一侧（不要改 DESIGN.md）：

1. DESIGN.md L356 写「用户气泡：{colors.brand} 底 + 白字」，
   Chat.vue L727-730 实际是背景 --el-color-primary-light-9、文字继承深色。
   → 按 DESIGN.md 改代码：background: var(--el-color-primary); color: #fff。
2. DESIGN.md L369-373 写引用芯片「白底 + 1px 边框，文件名用 {colors.ink}、相似度用 {colors.ink-muted}」，
   Chat.vue L797-806 实际是整枚芯片统一 #303133，没做两级区分。
   → 按 DESIGN.md 改代码：把「文件名 · 90%」拆成两个 span，
     文件名 var(--app-ink)，相似度 var(--app-ink-muted)，中间的分隔符也用 muted。
3. DESIGN.md L243-244 只允许 20/15/14/12 四级字号（13px 仅限表格内正文），
   但 13px 已经扩散到多个非表格位置。
   → 这一步不自己判断，先给我一份清单：所有 13px 出现的位置（文件:行 + 用途），
     我来决定哪些收敛回 12/14、哪些保留并写进规范。

除此之外，再找出所有「DESIGN.md 明确禁止但代码里存在」的情况，逐条列给我（不要改）：
  - box-shadow 的使用位置
  - 不在色板内的字面色值
  - 与 20/15/14/12 四级不符的字号
  - 圆角不按「容器12 / 卡片8 / 条目6 / 按钮4」分层的位置
  - 一屏出现三种以上语义色的页面

输出格式：一个表格，列 = 文件:行 / 规范条款 / 实际写法 / 建议动作（改代码 / 改规范 / 待定）。
```

---

## 五、如果只做三件事

按性价比排序，前三条做完，观感提升 80%：

1. **提示词 1**（Diagnostics 健康态）—— 单页改动，视觉改善最明显，一页从"没做完"变成"做完了"。
2. **提示词 2**（KbsList 操作区）—— 顺手修掉语义色误用，卡片从"挤成一团"变成"干净"。
3. **提示词 5**（token 收敛）—— 零风险、全站受益，而且是后面所有页面不再漂移的前提。

`DESIGN.md` 本身不用大改 —— 它已经是这个项目最好的资产之一。缺的只是**执行到底**，
以及把 P2-11 里那几处自相矛盾的地方对齐掉：**规范失效比某一页丑危险得多**，因为下一个 AI 会照着错的那版改。
