# 需求卡片：K5-答案反馈闭环

> 每张卡片 = 一个功能积木。实现前先填好，做完后在 `progress-log.md` 更新状态。

## 一句话
对话页每条 AI 回答下方可以点「有用 / 没用」，点「没用」可再选原因标签；
反馈落库并关联到具体 ChatMessage，为将来的低分答案分析攒真实数据。

## 输入 / 输出
- 输入：`POST /api/kbs/{kb_id}/messages/{message_id}/feedback`
  `{rating: "up"|"down"|null, reason?: 标签}`（rating=null = 清除反馈）；
  `GET /api/kbs/{kb_id}/feedback?limit=` 按 KB 拉最近反馈列表；
  `GET /api/config/chat` 的 `ChatOptions` 增加 `feedback_reasons`（原因标签唯一来源）。
- 输出：会话详情里每条 assistant 消息带 `feedback` / `feedback_reason`（刷新后状态不丢）；
  对话页答案下方出现「有用 / 没用」文字按钮 + 「没用」后的原因 chips。

## 验收标准（可测试，逐条打勾）
- [x] 反馈 upsert：同一条消息重复反馈是**更新**而不是插入新行（(message_id, user_id) 唯一）
- [x] rating=null 清除已有反馈；reason 不在标签清单 → 422；rating 非法值 → 422
- [x] 只能评价自己会话里的 assistant 消息：他人会话 403、跨 KB 404、user 消息 400
- [x] 会话详情返回当前用户的 feedback / feedback_reason（不泄露他人反馈）
- [x] `GET /api/kbs/{id}/feedback` 返回按时间倒序的反馈 + 答案预览（viewer+ 可读）
- [x] 前端：有用/没用可切换可取消；没用后出现原因 chips，点选即上报；刷新后状态正确
- [x] 后端 pytest 与前端 vitest 全绿；`vue-tsc` / `vite build` 通过

## 依赖
- 前置积木：`J2-会话持久化`（反馈挂载在 ChatMessage 上，K7 的 `/api/config/chat` 顺带下发原因标签）

## 不做什么（边界，防止范围蔓延）
- 不做反馈统计看板 / 低分答案分析（等数据攒起来另开卡）
- 不做自由文本原因（只有标签清单，聚合才可用）；不做对 user 消息的反馈
- 不做反馈后的答案重生成

## 失败点 / 风险
- 新表 `message_feedback` 靠 `create_all` 建表，**零迁移**；唯一约束防重复行
- 反馈列表查询要 join message → conversation 才能按 KB 过滤；预览截断防大 payload
- 前端本地状态与服务端返回对齐：以 POST 响应为准更新消息，不自行推算
