# 需求卡片：L3-扫描件 OCR

> 排期依据：阶段六（2026-10-07 访谈定案），L1 / L2 之后收官块。实现前填好，做完在 `progress-log.md` 记状态。
> **状态：2026-10-26 实现完成 ✅**（Docker 镜像重建验证因本机 Docker 未运行而挂起，见验收标准末条）

## 一句话

纯图扫描 PDF（每页抽出的文字极少）自动 fallback 到 RapidOCR 识别 —— 挂在 `_parse_pdf`
内部，同步 `ingest_file` 与 K4 异步摄取两条路径同时覆盖、管线零改动；扫描件从
「解析为空 → failed：文档解析后无有效内容」变成「能被正常检索」。

## 背景

`_parse_pdf` 只走 pypdf 文字层。扫描件（拍照 / 图片导出的 PDF）没有文字层，
`extract_text()` 每页空串 → 解析产物为空 → `文档解析后无有效内容` failed。
这占了真实企业文档的相当比例（合同扫描件、盖章页、老档案）。

## 关键设计决策（实现前定死，改动前先读）

1. **OCR 放在 `_parse_pdf` 内部，不新增管线阶段**。两条摄取路径（同步 `ingest_file` /
   异步 `_run_job`）都调 `parse_file(path)`，放解析层内部 = 一处改动两路覆盖，
   K4 的阶段表 / 进度上报**零改动**（OCR 耗时自然计入 `parsing` 阶段）。
2. **按页判定，不按整本平均**。逐页 `extract_text()`，某页字符数 <
   `OCR_MIN_TEXT_CHARS`（默认 20）→ 该页进入 OCR 候选。混合 PDF（文字正文 +
   图片封面）只 OCR 封面那一页，成本与需求成比例。
3. **绝不回归「未装 OCR」的现状**（分三档）：
   - 有弱页 + OCR 可用 → 弱页逐页 OCR，识别文本**替换**该页原（近乎为空的）文本；
   - 有弱页 + 有强页 + OCR 不可用 → 跳过弱页，行为与改动前完全一致
     （带图片封面的文字 PDF 在未装 OCR 的环境里照样摄取成功）；
   - **全部**页都弱 + OCR 不可用/关闭 → 诚实失败，错误信息点名「疑似扫描件 +
     需要 OCR 组件 / OCR 未启用」（此前是笼统的「文档解析后无有效内容」——
     同样失败，但用户现在知道为什么、该怎么办）。
4. **OCR 引擎进程级单例 + 线程锁**。RapidOCR 构造要加载 onnx 模型（秒级），
   摄取跑在 K4 线程池里 —— `app/ingestion/ocr.py` 惰性建一次，推理加 `threading.Lock`
   串行化（onnxruntime session 本身线程安全，串行是保守兜底，OCR 场景吞吐本就不高）。
5. **渲染用 pypdfium2，不用 PyMuPDF / pdf2image**。pdfium 走 Apache/BSD 授权
   （PyMuPDF 是 AGPL），wheel 自带 pdfium 二进制（pdf2image 要系统装 poppler）——
   纯 pip 可装、Windows 开发机与 Docker 两头都不用装系统包。
   渲染走 `bitmap.to_numpy()`（numpy 由 opencv 依赖带入），**不引入 Pillow**。

## 配置

| 项 | 默认 | 说明 |
|---|---|---|
| `OCR_ENABLED` | `true` | 总开关。未安装 rapidocr / pypdfium2 时自动降级（决策 3），不报启动错误 |
| `OCR_MIN_TEXT_CHARS` | `20` | 单页判定线：`extract_text()` 去空白后字符数低于它 → 该页 OCR |

渲染 scale 内部钉 2.0（≈144 DPI，实测对印刷体足够），不做成配置 —— 旋钮越少越好。

## 依赖与分发

- 新增 `[ocr]` extra：`rapidocr-onnxruntime` + `pypdfium2`（模型内置在 wheel 里，
  **安装后离线可用**，测试与生产都不联网下载模型）
- 本地开发：`pip install -e ".[dev,api,ocr]"`；CI：安装串改为 `.[dev,api,ocr]`
  （ubuntu CI 多下 ~200MB，换 OCR 测试真跑）
- Dockerfile：依赖抽取行 `dependencies + [api]` 改为 `+ [api] + [ocr]`
  （镜像 +200~300MB，排期时已接受）
- `pip install -e .`（无 extra）与 `.[dev]` 的环境**行为不变**：OCR 测试自动 skip
  （pgvector 用例同款闸门模式），业务代码走决策 3 的降级分支

## 输入 / 输出

- 输入：`.pdf` 上传件（其余格式不动）
- 输出：弱页被 OCR 文本替换后的完整文档文本 → 走既有 chunker / embedding / 索引链路

## 验收标准（可测试，逐条打勾）

- [x] 纯图扫描 PDF（fixture）：装 OCR 时端到端摄取成功，切片内容含关键句
- [x] 未装 OCR 时纯图 PDF → failed，错误信息含「疑似扫描件」与「rapidocr-onnxruntime」
      （可操作的指引，不是笼统的「无有效内容」）
- [x] 文字页 ≥ 阈值 → 该页**不进** OCR（引擎 spy 断言零调用）
- [x] 混合 PDF（文字页 + 弱页）在未装 OCR 时行为与改动前一致（弱页跳过、摄取成功）
- [x] `OCR_ENABLED=false` 时不触发 OCR（等价于不可用路径）
- [x] OCR 测试在未装 rapidocr 的环境自动 skip（闸门测试钉住这个行为）
- [x] 后端全量 pytest 0 failed（新增用例带 skipif 闸门；门后真跑 OCR）
- [x] 前端 vitest + build 全绿（本块不动前端，回归确认）
- [x] 新增断言做过反向验证（把阈值判定改坏 / 断言改错，确认变红）
- [x] ⚠️ ~~Docker 镜像重建验证挂起~~ → **2026-10-26 已补**：镜像含 `[ocr]` 重建成功、
      三容器 healthy、`docker_smoke.py` 10 组断言全过、**扫描件经 18080 真实上传 →
      indexed / chunks=1**。过程中照出并修复 bugfix #50（opencv 缺 libxcb/libgl 原生库，
      被 `available()` 降级吞成「未安装」—— Dockerfile apt 层补 `libgl1 libglib2.0-0 libxcb1`，
      `available()` 改为记录 ImportError 详情）

## 实测数字（2026-10-26）

- 后端：**489 collected / 376 passed / 0 failed / 113 skipped**（482 → 489，新增 7 例，
  本机装了引擎全量真跑；未装 `[ocr]` 的环境 7 例自动 skip）
- 前端：**74 全绿 + `vue-tsc && vite build` exit 0**（本块不动前端，纯回归）
- 反向验证：废掉弱页判定 → **4 例变红** → 恢复 → 7/7 绿
- 真 OCR 实测：密排语料页 5.9s/页（scale 2.0）；fixture 关键句（电镀槽 / 五微米 /
  设备巡检制度）逐字命中；端到端（上传 → indexed → chunk_count>0）通过

## 依赖

- 前置积木：A6（pypdf 解析）、K4（异步摄取——OCR 的耗时天然被异步吸收）、
  K8（降级路径留痕的日志纪律）

## 不做什么（边界，防止范围蔓延）

- 不做 DOCX / XLSX / 图片文件的 OCR（图片格式不在上传白名单；扫描件的主形态就是 PDF）
- 不做 OCR 结果的后处理（版面还原、表格结构化）—— 逐行文本足够检索用
- 不做「OCR 质量分数」配置与人工校对界面
- 不做 PPT 解析（留在候选池）
- 不改 `SUPPORTED_EXTS`、不改前端

## 失败点 / 风险

- **OCR 识别质量**：手写 / 模糊 / 倾斜扫描件可能识别出低质量文本并**成功入库** ——
  比静默失败好（能检索到总比检索不到强），但答案质量会受影响；RapidOCR 自带置信度
  过滤，不做二次处理（边界已画）
- **性能**：渲染 2.0 scale + OCR 单页秒级，百页扫描件摄取可能几分钟 —— K4 异步 +
  `parsing` 阶段可见，UI 不阻塞；这是排期时接受的代价
- **opencv-python 体积**（rapidocr 的依赖，~60MB）：已计入镜像增量预算；它同时把
  `cv2` 带进运行环境，本仓不直接用 cv2，不与之发生交互
- **阈值 20 是印刷体经验值**：过小的正文字号或稀疏排版（如纯目录页）可能被误判为弱页
  → 多 OCR 一页，代价是多花时间、结果仍是该页正确文本，无正确性风险
- **Windows 字体依赖在 fixture 生成侧**：生成脚本用 `C:\Windows\Fonts\simsun.ttc`
  渲染中文；**fixture PDF 已提交进仓**，CI 与其他机器不需要再生成
