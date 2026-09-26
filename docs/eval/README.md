# K3 答案质量评测记录

本目录是 `scripts/k3_eval.py` 的产出，用于回答一个具体问题：**K3 改了 prompt 结构、
加了来源元信息之后，答案到底变好没有、哪一处起了作用。**

## 怎么跑

```powershell
# 最终配置（enable_thinking=false）
.\.venv\Scripts\python.exe scripts\k3_eval.py --tag "after" --kb 1 `
    --out docs\eval\k3-after.md --json docs\eval\k3-after.json

# 对照：开思考
$env:LLM_ENABLE_THINKING="true"   # PowerShell
.\.venv\Scripts\python.exe scripts\k3_eval.py --tag "after-think" --kb 1 `
    --out docs\eval\k3-after-think.md --json docs\eval\k3-after-think.json

# 基线（把 app/ 切回 K3 之前的版本再跑，脚本对新旧两版代码都能跑）
git checkout 9333077 -- app/
.\.venv\Scripts\python.exe scripts\k3_eval.py --tag "before" --kb 1 `
    --out docs\eval\k3-before.md --json docs\eval\k3-before.json
git checkout HEAD -- app/
```

## 固定问题集

13 问固定不变：**10 问库内**（含「皮具类五金件」这种需要跨两份文档拼的，
以及故意刁难检索的「一共 4 份文档挑 1 份」）、**3 问库外**（差旅报销 / 年假 /
天气，考察会不会硬编）。

指标全部由程序判定，不靠人眼打分：`检索命中@1`、`检索命中@3`、`答案点名文档`、
`正确拒答`、端到端耗时。

## 结论

### 1. prompt 重构 → 答案从「资料显示」变成「根据《XX制度》4.2 节」

| 配置 | 答案点名文档 | 检索命中@1 | 正确拒答 | 平均耗时 | 空/截断答案 |
|---|---|---|---|---|---|
| baseline（K3 之前） | **0/10** | 9/10 | 3/3 | 7661 ms | 1 空 |
| K3-after（关思考） | **10/10** | 9/10 | 3/3 | **4137 ms** | 0 |
| K3-after（开思考） | 9/10 | 9/10 | 3/3 | 10616 ms | 0 |

`检索命中@1` 三行完全一致（9/10）——**检索侧没动，符合预期**；K3 只改「拿到资料后怎么答」。
这一点是刻意的：把答案变好和检索变好分开归因，别混在一个数字里。

`答案点名文档` 0/10 → 10/10 是本次最直接的成效。基线失败的原因不是模型不会写，
而是 **prompt 里根本没有文档名**——`_attach_filenames` 在生成之后才跑，
模型手上只有 `[1]`，不可能写出「根据《供应商管理制度》4.2 节」。

### 2. 意外收获：推理模型的 reasoning token 会挤空正文

`qwen3.8-flash` 是推理模型，`reasoning_tokens` 与正文**共用同一份 `max_tokens` 预算**：

```
同一问题、同一 prompt，只改 max_tokens：
  1024 → finish_reason=length, reasoning=922, 正文 153 字（截在句子中间）
  2048 → finish_reason=stop,   reasoning=935, 正文 401 字
```

基线那一轮第 9 问「物流破损导致客户投诉」直接返回了 **空答案**
（1024 个 token 全被 reasoning 吃光，HTTP 却仍是 200）。这不是 K3 引入的缺陷，
是既有问题第一次有了评测把它照出来。

处置：
- `LLM_MAX_TOKENS` 默认 1024 → **2048**（原先是硬编码，正好被 K3 的配置化收拾掉）
- `OpenAICompatLLM.complete()` 遇到 `finish_reason=length` 记 **WARNING** 日志
  （HTTP 200 的静默截断，不告警只能靠用户看到半截答案才发现）
- `Generator` 在模型返回空白时给 `LLM_EMPTY_ANSWER` 而不是空串
  （与 `NO_HIT_ANSWER` 分开：一个是「资料里没有」，一个是「模型没答上」）

### 3. `enable_thinking` 开关：关掉思考既快 2.6 倍、指标还更好

K3 的 prompt 要求更结构化的输出（结论 + 依据 + 文档名 + 章节号），推理量随之上涨，
2048 又会偶发被吃满。于是找了一圈推理控制：

| 手段 | 结果 |
|---|---|
| `max_tokens=4096` | 17.9s，能出，但只是拿钱买回来的 |
| `enable_thinking=false` | **6.3s**，正文 635 字，更完整 |
| `thinking_budget=512` | 中转站 400 `UNKNOWN_FIELD`（不支持） |

13 问整轮对比见上表：**4.1s/问 vs 10.6s/问，且关思考那轮点名率 10/10 高于开思考的 9/10**。
原因是关掉思考后预算全留给正文，不再被思维链占掉。

`LLM_ENABLE_THINKING` 设计成三态字符串（`""` / `true` / `false`）：留空 = **不下发该字段**，
兼容不认识它的供应商（实测多发字段会被中转站 400 顶回来）；
用字符串而非 `bool | None` 是因为 `.env` 里写 `LLM_ENABLE_THINKING=`（留空）
在 `Optional[bool]` 下会直接启动报错。

## 已知遗留（不属本批，留给批 2 / 后续）

- **第 2 问检索未进前三**：「皮具类的五金件有什么标准？」期望命中《供应商管理制度》4.2 节，
  实际只排到第 4 位（模型仍答对了，因为 top_k=5 覆盖到了）。
  检索排序属 K3-4 阈值调整与 K4 的范围，本批不动检索逻辑。
- **`similarity_threshold=0.1` 仍偏低**：库外 3 问虽然都正确拒答，但检索侧照样返回了 5 条噪声
  （0.1 阈值等于不过滤）。K0 实测库内 0.70–0.79 / 库外 0.28–0.32，上调到 ~0.35 是安全的，
  但会改变召回，需单独评测后再动。
- **Q7 结尾少一个句号**：模型输出末尾偶发截断在标点前（非 `finish_reason=length`），
  影响可忽略，未处理。
