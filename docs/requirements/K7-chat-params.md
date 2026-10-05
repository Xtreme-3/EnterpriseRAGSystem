# 需求卡片：K7-对话页参数可视化

> 每张卡片 = 一个功能积木。实现前先填好，做完后在 `progress-log.md` 更新状态。

## 一句话
把质检台里「只有开发者看得见」的检索参数（top_k / 检索模式 / 重排开关）和模型切换
搬到对话页，提问时当场可调 —— 演示时检索能力第一次在主流程里可见。

## 输入 / 输出
- 输入：`POST /api/kbs/{id}/ask` 与 `/ask/stream` 请求体新增
  `top_k`（0–10，0 = 服务端默认）、`rerank`（bool | null，null = 跟随服务端配置）、
  `model`（字符串，须在 `/api/config/chat` 返回的 models 清单内）；
  新端点 `GET /api/config/chat` 返回 `{models, rerank, top_k_default}`。
- 输出：对话页头部参数栏（检索条数 / 模型下拉 / 重排开关，模式切换已有）；
  生成行为按请求参数变化（来源条数、是否重排、所用模型）。

## 验收标准（可测试，逐条打勾）
- [x] `AskRequest.top_k` 越界（>10 或 <0）→ 422；缺省 0 → 用服务端 `Settings.top_k`
- [x] `rerank` 三态语义与质检台一致：null 跟随 `Settings.rerank` / false 跳过重排 /
      true 强制重排（全局未启用时按需构建强制重排器并**缓存**，K2：只建一次）
- [x] `model` 不在配置清单 → 422；合法值贯通 `RagPipeline → Generator → LLMProvider`，
      `OpenAICompatLLM` 请求体的 `model` 字段被覆盖
- [x] `GET /api/config/chat` 返回服务端模型清单（`llm_model` 恒在首位）、重排可用性、默认 top_k
- [x] 前端参数栏三控件就位；`askStreamRequest` 把参数带进请求体；`npm test` / `npm run build` 通过
- [x] `MockLLM` 能吃下新增 `model` kwarg（mock 模式全链路不 TypeError）

## 依赖
- 前置积木：`K2-管线单例`（参数栏取配置走 `app.state.settings`；强制重排器只建一次）、
  `H1-混合检索`（mode 已贯通）、`I1-Rerank`（rerank 语义与质检台同源）

## 不做什么（边界，防止范围蔓延）
- 不做多模型**并发对比**、不做参数持久化（刷新恢复默认即可）
- 不做语义缓存（K6 已移入后续候选）、不动 chunk / 检索逻辑本身
- 模型清单只在服务端配置（`LLM_MODEL_OPTIONS`），前端不提供自由输入

## 失败点 / 风险
- 全局 `RERANK=false` 时强行 rerank=true：真实环境（中转站无 rerank 端点）会在调用期报错
  → 与质检台同款行为，报错走 K8 留痕（logger.exception + 502 / 流内 error 事件）
- 校验 model 清单**不能**在 pydantic validator 里读 `get_settings()`（会读本机 .env，
  测试行为随机）→ 放在路由层对照 `app.state.settings`，测试经由 config 端点取合法值
- 分批交付：批 1 = top_k + rerank + 参数栏；批 2 = 模型切换（动 provider 契约，单独验收）
