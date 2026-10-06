# 需求卡片：K10-Excel 格式支持

> 每张卡片 = 一个功能积木。实现前先填好，做完后在 `progress-log.md` 更新状态。
> 2026-10-06 从「更多格式」候选拆卡（grill-me 定案：先只做 Excel，PPT 留作下一块）。

## 一句话
知识库能吃 Excel（.xlsx）：传一个报销标准表/供应商清单，按 sheet 切片入库，
对话页能引用「文件名 · sheet」回答表里的数字——支持格式从 4 种变 5 种。

## 输入 / 输出
- 输入：`.xlsx` 上传（走现有摄取链路：解析 → 切片 → 向量化）
- 输出：切片文本 = `## sheet 名` 小节 + 「列值 | 列值」行文本；溯源与引用同其他格式

## 验收标准（可测试，逐条打勾）
- [x] `_parse_xlsx`：sheet 名成为小节标题；空行跳过；空单元格剔除；
      数字/日期原样转文本（金额是检索命脉，不丢格式）
- [x] `SUPPORTED_EXTS` 注册 `.xlsx`，`.xlsx` 加入 `_MARKDOWN_EXTS`
      （解析产物是受控 markdown：仅含自写的 `## sheet` 标题，切片后由
      `clean_chunks` 剥前缀——避免 `##` 残留进答案，#41 同款教训）
- [x] 上传 API 接受 .xlsx（真实 openpyxl 生成的文件走完整摄取链路）
- [x] **前端 accept 列表改为后端下发**：新端点 `GET /api/config/upload` 返回
      `SUPPORTED_EXTS`；KbsList / DocList 删除各自的硬编码副本（消灭三处漂移源）
- [x] CI：GitHub Actions 跑后端 pytest + 前端 test/build，README 挂徽章
- [x] 双端测试全绿；真文件验收：scratch KB 传真实 xlsx → 提问命中表内数字

## 依赖
- 前置积木：`A6-文档解析`（注册表模式，新增解析器是纯增量）、`K7`（config 端点先例）

## 不做什么（边界，防止范围蔓延）
- 不做 .xls（旧格式，openpyxl 不支持，需另装 xlrd）——.xlsx/.xlsm 覆盖现实绝大多数
- 不做 PPT（留作下一块）；不做公式重算（`data_only=True` 读缓存值）
- 不做列头语义解析（列名不做 chunk 元数据）——第一版「值并列」已可检索
- 不做时间趋势之类看板扩展（G6 已收口）

## 失败点 / 风险
- 巨型 sheet 一次 `iter_rows` 内存可控（read_only 模式流式）
- 单元格以 `#` 开头会被切片器当标题——受控生成的行以列值并列，首列 `#` 开头
  属罕见边角；`clean_chunks` 对 .xlsx 剥 `#` 前缀后仅丢标题标记，不丢数据行
- CI 首跑可能暴露平台差异（PDF 渲染字体等）——失败按日志修，不掩盖
