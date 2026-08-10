# 需求卡片：B1-FastAPI 应用骨架

## 一句话
搭建 FastAPI 服务的最小骨架：能启动、有 `/health` 健康检查，为后续 API 积木提供统一基座。

## 输入 / 输出
- 输入：`uvicorn app.main:app` 启动
- 输出：
  - `GET /health` → `{"status": "ok"}`
  - 根路径 `GET /` → 服务基本信息

## 验收标准（可测试）
- [x] `uvicorn` 能启动服务，无报错
- [x] `GET /health` 返回 200 与 `{"status": "ok"}`
- [x] 未处理异常有统一 JSON 错误响应（不泄露堆栈）
- [x] 访问日志正常输出

## 验证结果（2026-08-06）

- `app/main.py`：FastAPI 骨架 + lifespan 初始化数据库 + `/health` + `/` + 全局 `Exception` 处理器 + 独立 `app` 日志记录器
- `tests/test_health.py`：3 个用例（/health、/、统一 500 JSON），用 `TestClient`（不进上下文管理器，跳过 lifespan，离线可跑）
- 实测 `uvicorn app.main:app --port 8001` 启动：lifespan 建表成功，`/health`→`{"status":"ok"}`，`/`→服务信息，404→JSON，`/docs`→200，访问日志正常输出
- Windows 日志编码：stderr/stdout 重配置为 UTF-8，避免中文日志乱码/崩溃（与 demo.py 一致）
- **16 测试全绿**（13 + B1 3 个）
- 备注：本机 8000 端口被 workbuddy 的 pythonw 进程占用，验证改用 8001

## 依赖
- 前置积木：A1–A9（已完成，本积木只复用配置与存储，不加新功能）

## 不做什么（边界）
- 本积木不做：任何业务接口（知识库/文档/聊天都不加），不加鉴权
- 本积木不做：数据库初始化之外的存储改动

## 失败点 / 风险
- FastAPI 在 Python 3.13 下的版本兼容（当前未安装 fastapi，需先装）
- 端口占用、Windows 下 uvicorn 启动方式
