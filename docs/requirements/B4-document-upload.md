# 需求卡片：B4-文档上传 + 向量化 API

## 一句话
上传文件到知识库 → 自动解析、切片、向量化、入库，并查询处理状态。

## 子块

| 子块 | 功能 | 说明 |
|------|------|------|
| B4-1 | 文档上传 `POST /api/kbs/{id}/documents` | multipart 上传 → 同步解析→切片→向量化→入库 |
| B4-2 | 状态查询 `GET /api/documents/{id}` | 返回文档状态、切片数、错误信息 |

## 输入 / 输出

- `POST /api/kbs/{kb_id}/documents` → multipart/form-data `file` → `{"id": 1, "filename": "...", "file_type": "...", "status": "indexed", "chunk_count": 5, ...}`
- `GET /api/documents/{doc_id}` → `{"id": 1, "filename": "...", "status": "indexed|failed", "chunk_count": 5, "error": null, ...}`

## V1 不做什么

- 不做异步队列（当前同步处理，文件大会等几秒）
- 不做断点续传、分片上传
- 不做文档更新/替换
- 不做批量上传

## 验收标准（可测试）

- [x] 上传 PDF/TXT/MD 文件返回 201，status=indexed
- [x] 上传到不存在的知识库返回 404
- [x] 上传到别人的知识库返回 403
- [x] 未登录上传返回 401
- [x] 上传不支持的文件类型返回 400
- [x] 上传空文件返回 400
- [x] 查询文档状态返回正确 status/chunk_count
- [x] 查询不存在的文档返回 404
- [x] `pytest` 全部通过（44 测试全绿）

## 验证结果（2026-08-07）

- `app/api/documents.py`：2 端点（upload multipart → ingest_file + get status），复用 `_get_user_kb_or_403` 权限校验
- `app/ingestion/pipeline.py`：`ingest_file` 新增 `display_name` 参数保留原始文件名；`_fail` 适配 filename
- `app/main.py`：挂载 documents_router
- `tests/test_documents.py`：10 个测试（txt/md 上传、KB 不存在、跨用户、未登录、类型不支持、空文件、状态查询、文档不存在、跨用户查看）
- 临时文件自动清理（成功/失败均清理）
- **44 测试全绿（34 + 10 新增文档测试）**

## 依赖

- 前置：B3（知识库 CRUD），复用 `IngestionPipeline.ingest_file`
- 文件格式：PDF/DOCX/MD/TXT（复用 A6 解析器）

## 失败点 / 风险

- 大文件同步处理可能导致请求超时（V1 不做异步，后续进阶处理）
- 临时文件需要清理
- PDF 可能解析为空文本
