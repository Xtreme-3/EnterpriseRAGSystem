# 前端去 AI 味设计指令

> 每次生成前端页面时遵守以下规则。AI 生成的页面有一组固定的"味儿"——本指令的目的就是消除这些痕迹，让页面看起来像人做的。

---

## 零、总则

任何设计决策都必须有理由。每个颜色、圆角、字号、间距都要给出一个具体的设计意图。

页面给人的感觉应该是：**这是一个有品牌性格的产品，不是 Vercel/Linear 模板的克隆。**

---

## 一、色彩 [优先级: 最高]

### 禁止
- `from-purple-600 to-blue-500` 或任何紫蓝渐变做 hero 背景
- 全站只用一种品牌色铺满
- 深色模式用纯黑 `#000000`

### 必须
- 选一个**非紫色**品牌主色（墨绿、赭石、琥珀、深灰、陶土红等）
- 主色只占页面 5-10% 面积，其余用中性色
- 如果非要用渐变，只做**同色系深浅渐变**（`#0f6e56` → `#04342c`）
- 深色模式背景色必须偏暖（`#1a1a18` 而非 `#000000`）

### 色盘模板
```css
:root {
  --bg-page:        #fafaf9;
  --bg-surface:     #ffffff;
  --border:         #e7e5e4;
  --text-primary:   #1c1917;
  --text-secondary: #78716c;
  --brand:          #0f6e56;    /* 墨绿，选什么颜色都行——但不能是紫色 */
  --brand-hover:    #085041;
  --brand-light:    #e1f5ee;
}
```

---

## 二、布局

### 禁止
- `text-align: center` 堆叠标题+副标题+描述
- `grid-template-columns: repeat(3, 1fr)` 三列等宽并排
- 所有模块等高、等宽、等间距

### 必须
- 标题左对齐（除非有明确的居中理由）
- 至少一处用不等分网格：`2fr 1fr`、`7fr 5fr`、`3fr 2fr`
- 至少一个元素"出格"——负 margin 偏移、与相邻元素重叠 8-16px
- 相邻模块高度不同、宽度不同

```css
.hero {
  display: grid;
  grid-template-columns: 7fr 5fr;
  align-items: center;
}
.features {
  display: grid;
  grid-template-columns: 2fr 1fr 1fr;
}
.accent-block {
  margin-top: -20px;
  margin-left: -12px;     /* 出格，打破完美栅格 */
}
```

---

## 三、字体

### 禁止
- 全文只用 Inter / Geist / SF Pro 一种字体
- 字重全是 400/500（看起来平的）
- 标题和正文差距小于 1.5 倍

### 必须
- **双字体搭配**：标题用衬线体/展示体，正文用无衬线体
- 字重跨度大（标题 600-700，正文 400）
- 标题字号是正文的 2-3 倍

### 推荐搭配
| 标题 | 正文 | 适用 |
|------|------|------|
| Fraunces | Inter | 英文产品/编辑 |
| Playfair Display | Source Sans 3 | 英文优雅风 |
| Noto Serif SC | Noto Sans SC | 中文通用 |
| 思源宋体 | HarmonyOS Sans | 中文产品 |

```css
h1, h2 { font-family: 'Fraunces', 'Noto Serif SC', serif; font-weight: 400; }
body   { font-family: 'Inter', system-ui, 'Noto Sans SC', sans-serif; }
h1     { font-size: 48px; }  /* 正文 3 倍 */
p      { font-size: 16px; }
```

---

## 四、圆角

### 禁止
- 全站统一圆角（如所有元素 `rounded-2xl`）
- 按钮圆角 ≥ 卡片圆角

### 必须
- 圆角按层级递减：

| 层级 | 值 | 用在 |
|------|-----|------|
| L1 | 12-16px | 页面主容器 |
| L2 | 8px | 卡片 |
| L3 | 4-6px | 按钮、输入框 |
| L0 | 0px | 图片、分割线 |

```css
.container { border-radius: 16px; }
.card      { border-radius: 8px; }
.btn       { border-radius: 4px; }
img        { border-radius: 0; }
```

---

## 五、文案

### 禁止词（出现就删）
powerful, seamless, leverage, robust, cutting-edge, next-generation, empower, revolutionize, innovative, best-in-class

### 替换原则
每个形容词后跟具体数字或场景：

| ✗ 不要说 | ✓ 要说 |
|----------|--------|
| Seamless integration | Connect to Slack and Notion in 3 min |
| Powerful analytics | Spot revenue drops before they cost you |
| Empower your team | Save 2 hrs of form-filling every day |
| 强大的数据分析能力 | 11 月 GMV 异常波动，在客户投诉前自动告警 |

---

## 六、动效

### 禁止
- 无任何 hover/transition（页面像死的）
- `transition: all 0.3s` 不加区分
- 一个元素 hover 时 `scale + rotate + shadow` 全上

### 必须
- 所有可交互元素有 hover 反馈，**只用 1 个属性变化**
- 过渡用 `ease`，时长 ≤200ms

```css
.card { transition: transform 0.15s ease; }
.card:hover { transform: translateY(-2px); }

.btn { transition: background-color 0.15s ease; }
.btn:hover { background-color: var(--brand-hover); }

/* 加载：骨架屏 > spinner */
@keyframes shimmer {
  0%   { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
.skeleton {
  background: linear-gradient(90deg, #f0f0f0 25%, #e8e8e8 37%, #f0f0f0 63%);
  background-size: 200% 100%;
  animation: shimmer 1.5s ease infinite;
}
```

---

## 七、阴影（补充规则）

- 不用 `box-shadow` 做立体效果，用**边框颜色深浅**来区分层级
- 如果必须用阴影：`0 1px 2px rgba(0,0,0,0.04)`，极轻
- 禁止 `0 10px 40px rgba(0,0,0,0.1)` 这种厚重阴影

---

## 八、出格自检清单

生成页面后逐条检查：

1. [ ] 页面有没有紫蓝渐变？→ 有就换掉
2. [ ] 是不是全场同一种字体？→ 标题加衬线
3. [ ] 是不是所有圆角一样大？→ 分层混用
4. [ ] 有没有三张等宽卡片并排？→ 改不等分
5. [ ] 文案有没有禁止词？→ 换成具体描述
6. [ ] hover 有没有反馈？→ 加一个轻量过渡（0.15s, 1 个属性）
7. [ ] 整体看起来像不是 Vercel/Linear 模板？→ 回头重读总则
8. [ ] 能不能说出每个设计决策的理由？→ 说不出 = 重来

---

## 九、使用方式

生成页面时把本文件作为系统指令注入：

```
请根据以下前端设计指令生成页面：
--- 附上前端去AI味设计指令.md 全文 ---
```

或在 AI 对话开头说：**"生成前端页面时遵守「前端去AI味设计指令.md」"**
