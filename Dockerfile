# API 服务镜像。
# 前端镜像见 frontend/Dockerfile（构建 SPA + nginx 反代）。
# 二者由根目录 docker-compose.yml 编排，外加一个 pgvector 数据库。
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

# curl 供 HEALTHCHECK 使用；psycopg2 走 binary 轮子，无需编译工具链
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./

# 从 pyproject.toml 抽取依赖清单后安装 —— pyproject.toml 仍是唯一依赖来源，
# 但改动 app/ 不会让这层缓存失效，省下每次重装 chromadb 的时间。
RUN python -c "import tomllib,pathlib; p=tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8')); print('\n'.join(p['project']['dependencies'] + p['project']['optional-dependencies']['api']))" > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt

COPY app ./app

EXPOSE 8000

# 健康检查打的是根路径 /health（不在 /api 前缀下）
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
