# Prismatica 后端镜像(Flask + Gunicorn)
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# 系统依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖(利用 Docker 缓存)。手工清单必须覆盖 pyproject.toml 的全部运行时依赖,
# tests/test_deploy_dependencies.py 会阻止两处依赖再次漂移。
COPY pyproject.toml ./
RUN pip install --upgrade pip \
    && pip install \
        "Flask>=3.0.3" \
        "SQLAlchemy>=2.0.30" \
        "PyMySQL>=1.1.1" \
        "cryptography>=42.0.0" \
        "PyJWT>=2.8.0" \
        "bcrypt>=5.0.0" \
        "pydantic>=2.7.0" \
        "pydantic-settings>=2.3.0" \
        "loguru>=0.7.2" \
        "Flask-Limiter>=3.8.0" \
        "flask-cors>=6.0.0" \
        "redis>=5.0.4" \
        "prometheus-client>=0.20.0" \
        "gunicorn>=22.0.0" \
        "requests>=2.34.2" \
        "flasgger>=0.9.7.1"

# 复制源码
COPY app ./app
COPY scripts ./scripts
COPY wsgi.py ./wsgi.py

EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/healthz || exit 1

# Gunicorn 启动
CMD ["gunicorn", \
     "-w", "4", "-k", "gthread", "--threads", "2", \
     "-b", "0.0.0.0:8000", \
     "--timeout", "60", "--graceful-timeout", "30", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "wsgi:app"]
