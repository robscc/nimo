FROM python:3.11-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY pyproject.toml .
RUN pip install --no-cache-dir -e "." \
    && pip install --no-cache-dir gunicorn

# 复制源码
COPY agentpal/ ./agentpal/

# 数据持久化目录
RUN mkdir -p /app/data

ENV APP_ENV=production \
    DATABASE_URL=sqlite+aiosqlite:///./data/agentpal.db

EXPOSE 8088

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:8088/health || exit 1

CMD ["uvicorn", "agentpal.main:app", "--host", "0.0.0.0", "--port", "8088"]
