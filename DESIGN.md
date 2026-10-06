---
version: alpha
name: EnterpriseRAGSystem-design
description: "一套为「企业内部知识库问答」产品写的浅色设计系统。画布是冷灰 #f5f7fa，内容承载在纯白卡片上，靠 1px 边框 #e4e7ed 分层而不用阴影。唯一的彩色是墨绿 #0f6e56 —— 它只出现在品牌标识、主操作按钮、选中态和进度点上，面积不超过页面的 10%；其余 95% 是中性灰阶。标题用衬线（Noto Serif SC），正文用无衬线（Noto Sans SC），四级字号阶梯 20/15/14/12 收束全站层级。语义色（成功 #67c23a / 警告 #e6a23c / 危险 #f56c6c）只用于状态表达，绝不用于装饰。整体气质：克制的工具，不是营销页 —— 用户来这里是为了把文档变成可回答的东西，界面的任务是让答案和出处同时可信。"

colors:
  # ---- 品牌：墨绿 ----
  brand: "#0f6e56"            # 主操作、品牌标识、选中文字、进度点
  brand-hover: "#0c5845"      # hover / active（= --el-color-primary-dark-2）
  brand-soft: "#e7f1ee"       # 选中态底色、轻强调底（= --el-color-primary-light-9）
  brand-border: "#87b7ab"     # hover 边框、次级强调（= --el-color-primary-light-5）
  brand-line: "#b7d4cc"       # 更弱的线/描边（= --el-color-primary-light-7）
  # ---- 表面 ----
  canvas: "#f5f7fa"           # 页面底色（= --app-bg，也等于 --el-fill-color-light）
  surface: "#ffffff"          # 卡片、顶栏、表格、气泡
  surface-sunken: "#fafafa"   # 表头、代码块等"凹陷"面
  # ---- 线 ----
  hairline: "#e4e7ed"         # 卡片边框、分隔线（= --el-border-color-light）
  hairline-strong: "#dcdfe6"  # 表单控件边框（= --el-border-color）
  hairline-soft: "#ebeef5"    # 更浅的线（= --el-border-color-lighter）
  # ---- 文字 ----
  ink: "#303133"              # 标题与正文（= --el-text-color-primary）
  ink-secondary: "#606266"    # 次级正文、侧边栏条目（= --el-text-color-regular）
  ink-muted: "#909399"        # 说明、表头、统计卡标签（= --el-text-color-secondary）
  ink-disabled: "#c0c4cc"     # 占位、禁用、空态（= --el-text-color-disabled）
  # ---- 语义（仅状态表达）----
  success: "#67c23a"
  warning: "#e6a23c"
  danger: "#f56c6c"
  info: "#909399"

typography:
  family-display: "Noto Serif SC, Songti SC, SimSun, STSong, serif"
  family-body: "Noto Sans SC, PingFang SC, Hiragino Sans GB, Microsoft YaHei, Arial, sans-serif"
  page-title:
    fontFamily: "{typography.family-display}"
    fontSize: 20px
    fontWeight: 600
    lineHeight: 1.4
  card-title:
    fontSize: 15px
    fontWeight: 600
    lineHeight: 1.5
  body:
    fontFamily: "{typography.family-body}"
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.6
  hint:
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.6
    color: "{colors.ink-muted}"
  stat-number:
    fontSize: 26px
    fontWeight: 700
    lineHeight: 1.2
  brand-wordmark:
    fontFamily: "{typography.family-display}"
    fontSize: 18px
    fontWeight: 700
    letterSpacing: 0.5px

rounded:
  none: 0
  xs: 4px        # 按钮、输入框、Tag
  sm: 6px        # 列表条目、内联小块
  md: 8px        # 卡片、统计卡
  lg: 12px       # 页面级容器（弹窗、大面板）

spacing:
  unit: 16px     # 全站唯一间距单位
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  page-max: 1200px
  page-max-narrow: 960px

components:
  card:
    background: "{colors.surface}"
    border: "1px solid {colors.hairline}"
    rounded: "{rounded.md}"
    padding: 16px
  card-hover:
    borderColor: "{colors.brand-border}"
    transition: "border-color 150ms ease"
  stat-card:
    background: "{colors.surface}"
    border: "1px solid {colors.hairline}"
    rounded: "{rounded.md}"
    padding: 16px
    textAlign: center
  table-header:
    background: "{colors.surface-sunken}"
    color: "{colors.ink-muted}"
    fontSize: 13px
    cellPadding: 12px 0
  chat-bubble-assistant:
    background: "{colors.surface}"
    border: "1px solid {colors.hairline-soft}"
    rounded: "{rounded.lg}"
    padding: 12px 16px
    maxWidth: 78%
  chat-bubble-user:
    background: "{colors.brand}"
    color: "#ffffff"
    rounded: "{rounded.lg}"
    padding: 12px 16px
    maxWidth: 78%
  sidebar-item:
    rounded: "{rounded.sm}"
    padding: 8px 10px
    color: "{colors.ink-secondary}"
  sidebar-item-active:
    background: "{colors.brand-soft}"
    color: "{colors.brand}"
  primary-button:
    background: "{colors.brand}"
    color: "#ffffff"
    rounded: "{rounded.xs}"
    height: 32px
  ghost-button:
    color: "{colors.ink-secondary}"
    rounded: "{rounded.xs}"
    height: 32px
---

# EnterpriseRAGSystem — 设计系统（DESIGN.md）

> **这份文件是什么**：给 AI 编码助手读的设计系统说明。格式遵循 Google Stitch 的
> DESIGN.md 约定 —— 纯 markdown，无 Figma、无 JSON schema、无构建步骤。
> `AGENTS.md` 说明**怎么建这个项目**，本文件说明**它该长什么样**。
>
> **怎么用**：在项目根目录下让 agent 生成/修改任何前端页面时，它会自动读到本文件。
> 也可以直接把第九节的「即用提示词」复制给 agent。

---

## 一、Overview · 视觉气质

这是一个**浅色、克制、以文字为主**的工具界面。

页面基调由三个决定构成：

1. **靠底色和边框分层，不靠阴影。** 页面底色 `{colors.canvas}`（#f5f7fa）是冷的浅灰，
   所有内容都放在纯白 `{colors.surface}` 卡片上，卡片由 1px `{colors.hairline}`（#e4e7ed）围边。
   全站**不使用 box-shadow 做立体感**（唯一例外见第五节）。
2. **只有一个彩色。** 墨绿 `{colors.brand}`（#0f6e56）是全站唯一品牌色，
   出现在：品牌标识、唯一的主操作按钮、侧边栏选中态、流式进度点、图表强调值。
   它**不用于装饰**，面积控制在页面的 10% 以内。其余 95% 是中性灰阶。
3. **标题衬线、正文无衬线。** 页面标题、卡片标题、品牌标识用 `{typography.family-display}`
   （思源宋体体系），正文和控件用 `{typography.family-body}`（思源黑体体系）。
   这一对搭配是产品"有性格"的来源 —— **不要为了省事把标题也改成无衬线**。

**这个产品不是后台管理系统。** 10 个页面里只有 DocList 和 QaLogs 是真正的表格页；
核心是 Chat（对话页），另外还有卡片网格（KbsList）、开发者调试台（InspectBench）、
数据看板（Diagnostics / DocHealth）。**因此不存在"通用后台模板形态"这回事** ——
每一页按它自己的功能形状来排版。

**Key Characteristics：**

- 画布冷灰 `{colors.canvas}` + 纯白卡片 `{colors.surface}`，1px 边框分层，零阴影。
- 唯一彩色墨绿 `{colors.brand}`（#0f6e56），占比 <10%，只用于品牌与状态之外的主操作。
- 标题衬线 / 正文无衬线双字体系，标题字号为正文的 1.4–1.9 倍。
- 圆角严格分层：容器 12 → 卡片 8 → 条目 6 → 按钮 4 → 图片 0。
- 语义色（`{colors.success}` / `{colors.warning}` / `{colors.danger}`）只出现在 Tag、
  分数条、统计卡数字上，**按钮和标题一律不用**。
- 留白克制：16px 是唯一间距单位，紧凑处 8px。页面最大宽度 1200px 居中。

---

## 二、Colors · 色彩

### 品牌与强调

- **墨绿 Brand**（`{colors.brand}` #0f6e56）：主操作按钮底色、品牌标识文字、
  侧边栏选中文字、流式"正在处理"进度点、图表强调值。
- **Brand Hover**（`{colors.brand-hover}` #0c5845）：主按钮 hover / active 态。
- **Brand Soft**（`{colors.brand-soft}` #e7f1ee）：选中态底色、轻强调区块底。
- **Brand Border**（`{colors.brand-border}` #87b7ab）：卡片 hover 时的边框色。
- **Brand Line**（`{colors.brand-line}` #b7d4cc）：更弱的品牌描边。

> 完整色阶（Element Plus primary light-3/5/7/8/9 与 dark-2）已在
> `frontend/src/style.css` 里覆盖，新代码直接用 `--el-color-primary*` 变量，
> 不要写死十六进制。

### 表面

- **Canvas**（`{colors.canvas}` #f5f7fa）：页面底色 `--app-bg`。
- **Surface**（`{colors.surface}` #ffffff）：卡片、顶栏、表格行、AI 气泡。
- **Surface Sunken**（`{colors.surface-sunken}` #fafafa）：表头、代码块等"凹陷"面。

> ⚠️ **已知陷阱**：`#f5f7fa` 同时是页面底色和 Element 的 `--el-fill-color-light`。
> **绝不能**把它用作 AI 气泡的背景 —— 气泡会消失在页面里（Chat.vue 曾有此 bug）。

### 线与文字

- **Hairline**（`{colors.hairline}` #e4e7ed）：卡片边框、分隔线。
- **Hairline Strong**（`{colors.hairline-strong}` #dcdfe6）：输入框等表单控件边框。
- **Ink**（`{colors.ink}` #303133）：标题与正文。
- **Ink Secondary**（`{colors.ink-secondary}` #606266）：次级正文、侧边栏条目。
- **Ink Muted**（`{colors.ink-muted}` #909399）：说明文字、表头、统计卡标签。
- **Ink Disabled**（`{colors.ink-disabled}` #c0c4cc）：占位符、禁用态、空态提示。

### 语义（仅用于状态）

- **Success**（`{colors.success}` #67c23a）：已索引、命中、正常。
- **Warning**（`{colors.warning}` #e6a23c）：处理中、告警。
- **Danger**（`{colors.danger}` #f56c6c）：失败、删除、错误。
- **Info**（`{colors.info}` #909399）：中性标签。

> 这四色**只允许**出现在：状态 Tag、分数条、统计卡数字、错误提示文字。
> 不允许作为按钮底色（除非该按钮的语义就是"删除"）、不允许作为区块背景、
> 不允许同时出现三种以上。

---

## 三、Typography · 字体

### 字体族

- **Display**（`{typography.family-display}`）：`Noto Serif SC, Songti SC, SimSun, STSong, serif`
  —— 页面标题、卡片标题、品牌标识。
- **Body**（`{typography.family-body}`）：`Noto Sans SC, PingFang SC, Hiragino Sans GB, Microsoft YaHei, Arial, sans-serif`
  —— 正文、按钮、表格、表单。基础字号 14px。

字体通过系统字体栈生效，**不引入 web font**（保持零外部依赖）。因此设计时按
"思源宋体可用则用、不可用回退到系统宋体"来预期观感。

### 层级

**只准用这四级**，全站统一：

| 层级 | 字号 | 字重 | 用在 | 当前状态 |
|---|---|---|---|---|
| 页面标题 | 20px | 600 | 每页唯一的 h2（页面名） | ⚠️ 部分页面仍是 22px（KbsList） |
| 卡片标题 | 15px | 600 | 卡片头、区块标题、侧边栏标题 | ✅ 已一致 |
| 正文 | 14px | 400 | 正文、按钮、表格、表单 | ✅ 已一致 |
| 辅助 | 12px | 400 | 说明、表头、统计卡标签、时间戳 | ✅ 已一致 |

例外（允许存在，但不可扩散）：统计卡数字 26px/700、品牌标识 18px/700、
列表条目标题 16px/600、表格内正文 13px。

> **反模式**：h3=20px 紧挨 h2=22px、卡片标题 16px 而区块标题 15px —— 差 1-2px 的
> "层级"在视觉上等于没有层级，只会显得乱。要么同层，要么差 ≥4px。

---

## 四、Layout · 布局

### 刻度

- **唯一间距单位 16px**：页面内边距 16、卡片间距 16、卡片内边距 16。
- 紧凑处 8px（按钮组、标签行），大分隔 24px（区块之间）。
- 页面左右外边距：`layout-main` 统一 24px。

> ⚠️ 现状是 24/20/16/12/8 混用（`layout-main` 24px、Chat 消息间距 20px、
> stat-grid 12px）。**新代码一律用 16/8 两档**，旧代码分步收敛。

### 容器

| 场景 | 最大宽度 | 对齐 |
|---|---|---|
| 常规页面（知识库列表、对话页） | 1200px | 居中 |
| 单列窄页（诊断、体检） | 960px | 居中 |

### 页面骨架

顶栏固定 56px（`--radius` 不适用，直角），下方是内容区：

```
┌─ 顶栏 56px：品牌标识(左) · 用户名 + 角色 Tag + 退出(右) ────────────┐
├─────────────────────────────────────────────────────────────────┤
│  内容区（padding 24px，max-width 1200px 居中）                    │
└─────────────────────────────────────────────────────────────────┘
```

对话页在此骨架上多一条 **240px 会话侧边栏**（右侧留 20px 间距，1px 右边框分隔）。

### 网格

- 卡片网格：`repeat(auto-fill, minmax(320px, 1fr))`，间距 16px。
- 统计卡网格：`repeat(auto-fit, minmax(120px, 1fr))`，间距 12px。

> **不要用三列等宽并排**（`repeat(3, 1fr)`）来排功能卡 —— 那是"模板感"最强的信号。
> 卡片数量本就不定，用 auto-fill 让它自然流动。

---

## 五、Elevation & Depth · 层级与深度

**规则：用边框和底色分层，不用阴影。**

| 层级 | 做法 |
|---|---|
| 页面底 → 卡片 | 底色 `{colors.canvas}` → `{colors.surface}` + 1px `{colors.hairline}` |
| 卡片 → 凹陷块（表头） | `{colors.surface}` → `{colors.surface-sunken}` |
| 卡片 hover | 边框色变 `{colors.brand-border}`（**不动位置、不加阴影**） |
| 浮层（弹窗/下拉） | 允许 Element Plus 默认阴影，这是唯一例外 |

**禁止**：`0 10px 40px rgba(0,0,0,.1)` 这类厚重阴影；用阴影模拟"卡片浮起来"；
用渐变模拟光照。

---

## 六、Shapes · 形状

圆角按层级递减，**不允许全站统一圆角**：

| 层级 | 值 | 用在 |
|---|---|---|
| L1 | 12px | 页面级容器、弹窗、大面板 |
| L2 | 8px | 卡片、统计卡、消息气泡 |
| L3 | 6px | 列表条目、内联小块 |
| L4 | 4px | 按钮、输入框、Tag |
| L0 | 0 | 图片、表格行、分割线、顶栏 |

按钮圆角**必须小于**卡片圆角（4 < 8）。变量已就位：
`--radius-container` / `--radius-card` / `--radius-button`。

---

## 七、Components · 组件

### 卡片

```css
background: {colors.surface};
border: 1px solid {colors.hairline};
border-radius: {rounded.md};
padding: {spacing.md};
transition: border-color 150ms ease;
```
hover 时只把 `border-color` 改为 `{colors.brand-border}`。
**不要**同时加 `transform` + `box-shadow`。

### 统计卡（数据看板）

白底 + 1px 边框 + 8px 圆角 + 16px 内边距 + 文字居中。
结构固定为「大数字 26px/700」+「标签 12px `{colors.ink-muted}`」两行。
数字可按语义换色：正常 `{colors.ink}` / 好 `{colors.success}` /
坏 `{colors.danger}` / 强调 `{colors.brand}`。

> 两个看板页各自写了一份 `.stat-card`，**应抽成共用组件** `StatCard.vue`
> （数字 + 标签 + 可选状态色），否则必然漂移。

### 消息气泡（对话页）

- 布局用 **flex column + align-items**，**不要用 `float`**。
  用户消息右对齐、AI 消息左对齐，最大宽度 78%。
- **AI 气泡**：白底 `{colors.surface}` + 1px `{colors.hairline-soft}` 边框，
  12px/16px 内边距，圆角 12px。
  ⚠️ **不可用 `{colors.canvas}` 作气泡底色** —— 会和页面同色而消失。
- **用户气泡**：`{colors.brand}` 底 + 白字。
- **流式光标**：跟随最后一行文本的 `|`，品牌色，1s 闪烁。
- **阶段提示**（首 token 到达前显示）：14px `{colors.ink-muted}` + 左侧一个
  6px 品牌色圆点（1s 闪烁），文案依次为
  `正在改写问题…` → `正在检索资料…` → `正在重排…` → `正在组织答案…`。
  首个 token 到达后**立刻清空**阶段提示，换成逐字输出。
- **失败态**：13px `{colors.danger}`，附一句可重试的说明。

### 引用来源（本产品的核心卖点）

答案的可信度来自"有据可依"，所以**引用必须在答案打出前就可见**
（后端 `sources` 事件先于 `token` 事件下发）。

- 形态：答案下方一排**引用芯片**（不是折叠面板 —— 折叠等于把卖点藏起来）。
- 每个芯片显示 `文件名 · 相似度%`，点击展开该切片原文。
- 芯片本体：白底 + 1px 边框 + 4px 圆角 + 12px 文字；相似度用
  `{colors.ink-muted}`，文件名用 `{colors.ink}`。
- **不要**给每个芯片配不同颜色。

### 按钮

| 类型 | 样式 | 用在哪 |
|---|---|---|
| 主操作 | `{colors.brand}` 底 + 白字，32px 高，4px 圆角 | 每屏**只有一个**（发送、创建、进入对话） |
| 次操作 | 白底 + 1px `{colors.hairline-strong}` | 上传、导出 |
| 文字按钮 | 无底色，`{colors.ink-secondary}`，hover 变品牌色 | 行内操作、导航 |

> **反模式**：一张卡片上五个按钮分别用 primary/success/warning/默认/success 五种颜色
> （KbsList 曾如此）。**彩色只表达状态**，不表达"这里有五个功能"。
> 改成：一个主操作 + 其余全部灰色文字按钮。

### 表格（仅 DocList / QaLogs）

- 表头：`{colors.surface-sunken}` 底、13px、`{colors.ink-muted}` 文字
  —— 让用户第一眼看到数据而不是表头。
- 单元格上下 padding 12px（行高约 44px）。
- 状态列**必须用 Tag**：已索引=success / 处理中=warning / 失败=danger。
- 行内操作最多 2 个文字按钮，更多收进「更多」下拉。
- 长文本（如问答答案）用 `<pre>` + 等宽字体 + 最大高度 + 滚动。

### 侧边栏条目（对话页会话列表）

6px 圆角，`padding: 8px 10px`，12px（2026-10-06 从 13px 收敛进四级，消除与 §三的自相矛盾），`{colors.ink-secondary}`；
hover 底色 `{colors.canvas}`；选中项底色 `{colors.brand-soft}` + 文字 `{colors.brand}`。
删除图标默认 `{colors.ink-disabled}`，hover 变 `{colors.danger}`。

---

## 八、Do's and Don'ts · 硬约束

### Do

- 每个颜色、圆角、字号、间距都能说出**具体理由**。
- 主色只占页面 5–10%：一个主按钮 + 品牌标识 + 少量强调，就是它的全部用量。
- 标题用衬线体，正文用无衬线体。
- 靠底色（canvas → surface）和 1px 边框分层。
- 状态用语义色 Tag，且一屏内不超过三种状态色。
- 可交互元素都有 hover 反馈，且**只变一个属性**（边框色或底色），150ms `ease`。
- 加载态优先用骨架屏，其次才是 spinner。
- 文案里每个形容词后面跟具体数字或场景：
  「11 月 GMV 异常波动 → 客户投诉前自动告警」优于「强大的数据分析能力」。

### Don't

- **不要用紫蓝渐变**（`#6366F1` → `#5B8DEF` 这类），尤其不要做 hero 背景或登录页背景。
  这是"AI 生成感"的第一信号。
- **不要用 box-shadow 做层级**（唯一例外是 El Plus 弹窗/下拉）。
- **不要把 `{colors.canvas}`（#f5f7fa）用作气泡或卡片背景** —— 会和页面同色而消失。
- **不要全站统一圆角**（所有元素都 8px 或都 16px）。
- **不要三列等宽并排**功能卡。
- **不要在一张卡片上放多种颜色的按钮**。
- **不要堆叠居中标题 + 副标题 + 描述**作为页面开头（"模板 hero"形态）；
  标题左对齐，紧跟实际内容。
- 不使用纯黑 `#000000`（最深只到 `{colors.ink}` #303133）。
- 不引入 Tailwind / UnoCSS / 新组件库；不引 web font；不引图标字体大包。
- 不做深色模式（先把浅色做对）。

---

## 九、Agent Prompt Guide · 即用提示词

### 快速 token 速查

```
品牌墨绿   #0f6e56    hover #0c5845    选中底 #e7f1ee    hover 边框 #87b7ab
页面底色   #f5f7fa    卡片/气泡白  #ffffff    凹陷面 #fafafa
边框       #e4e7ed    表单边框 #dcdfe6    浅线 #ebeef5
文字       #303133 / #606266 / #909399 / #c0c4cc
语义       成功 #67c23a · 警告 #e6a23c · 危险 #f56c6c
圆角       容器 12 · 卡片 8 · 条目 6 · 按钮 4 · 图片 0
间距       只用 16 与 8；页面 padding 24；最大宽度 1200（窄页 960）；顶栏 56
字号       页面标题 20 / 卡片标题 15 / 正文 14 / 辅助 12
字体       标题 Noto Serif SC · 正文 Noto Sans SC
```

### 即用提示词

> 按本项目的 `DESIGN.md` 生成/修改页面。遵守：
> ① 主色只用墨绿 #0f6e56，面积 <10%，只用于品牌标识、唯一主操作、选中态、进度点；
> ② 分层靠底色 + 1px #e4e7ed 边框，零 box-shadow；
> ③ 圆角按 容器12/卡片8/条目6/按钮4 分层，禁止统一圆角；
> ④ 字号只用 20/15/14/12 四级，标题衬线、正文无衬线；
> ⑤ 间距只用 16 与 8 两档；
> ⑥ 状态色只用于 Tag / 分数条 / 统计卡，一屏不超过三种；
> ⑦ 先说明这一页**实际是什么形状**（对话页 / 卡片网格 / 调试台 / 数据看板 / 表格），
>   再决定排版 —— **不要套用后台管理模板的"筛选卡 + 表格卡"**。

### 改完之后自检

1. 有没有出现紫蓝？→ 有就换掉。
2. 有没有 box-shadow？→ 有就去掉（弹窗除外）。
3. 气泡/卡片底色是不是 #f5f7fa（和页面同色）？→ 是就改白底 + 边框。
4. 圆角是不是全站一样？→ 是就分层。
5. 一张卡上是不是有 3 个以上颜色的按钮？→ 收敛成 1 主 + N 灰。
6. 字号有没有出现 16/18/20/22 混着当标题？→ 归到四级里。
7. 间距有没有 12/20/24 混用？→ 归到 16/8。
8. 配色变了吗？如果这一页拿去截图，别人能否一眼看出是**同一个产品**？

---

## 十、Known Gaps · 现状与规范的偏差

> **2026-10-05：以下 9 项已全部清零**（三批提交，见 `docs/progress-log.md`）。
> 表格保留作为历史记录与对照基线 —— 新页面仍应对照本节理解"什么是偏差"。

| # | 项 | 状态 |
|---|---|---|
| 1 | `style.css` 尚未定义 `--app-gap` / `--fs-*` 刻度变量，间距与字号仍是散落的字面量 | ✅ 已定义（新代码用 token，旧代码分步收敛） |
| 2 | Chat AI 气泡背景 = 页面背景（`#f5f7fa`），气泡边界不可见 | ✅ 改白底 + 1px 浅边框 |
| 3 | Chat 气泡仍用 `float` + `::after clear`，未改 flex | ✅ flex column + align-items |
| 4 | 引用来源仍是 `el-collapse` 折叠面板，未改成引用芯片 | ✅ 芯片行 + 点击展开切片原文 |
| 5 | Chat 输入区按钮 `height: 60px` 硬编码 | ✅ `align-self: stretch` |
| 6 | KbsList 每卡 5 个多色按钮，违反"彩色只表达状态" | ✅ 「进入对话」唯一主色 + 其余灰文字按钮 |
| 7 | Diagnostics / DocHealth 各写一份 `.stat-card`，未抽公共组件 | ✅ `components/StatCard.vue` 两页共用 |
| 8 | 页面标题仍混用 22px 与 20px | ✅ 归一到 `var(--fs-page)` |
| 9 | 全站无响应式断点，窄屏（<1280px）下 240px 会话侧边栏会挤压对话区 | ✅ 断点收进「会话」el-drawer |

> ✅ 已做对、**不要推翻**的：卡片"白底 + 1px 边框 + 8px 圆角"、品牌墨绿色阶、
> 圆角分层变量、标题衬线/正文无衬线的双字体系、靠底色分层零阴影。

---

## 十一、Iteration Guide · 迭代方式

改前端时按这个顺序，**每步做完跑 `npm run build` 并肉眼过一遍页面，不要一次改完再验证**：

1. 先在 `frontend/src/style.css` 补刻度变量（`--app-gap` / `--fs-page` 等），
   这是零风险且全站受益的一步。
2. 再按 `docs/design-manifest.md` 的 P0 → P1 → P2 逐页改。
3. 每改一页，回来更新本文件第十节的状态标记。
4. 若本文件与 `docs/design-guide.md`（去 AI 味硬规则）冲突，**以 `design-guide.md` 为准**；
   与 `docs/design-manifest.md`（逐页方案）冲突时，本文件管"系统级规范"，
   manifest 管"这一页具体改哪几行"，两者不冲突。

**本文件是设计规范的唯一来源** —— 不要另立第二份设计文档。
