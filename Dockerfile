# ── Stage 1: 构建前端 ──────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# ── Stage 2: Python 运行时（后端 + 前端静态文件）──────────────
FROM python:3.11-slim

# 是否安装 Playwright Chromium（browser_use 工具需要，镜像 +500MB）
ARG INSTALL_PLAYWRIGHT=false

WORKDIR /app

# 安装 Python 依赖（利用 layer cache：先装依赖，再拷源码）
COPY backend/pyproject.toml ./
RUN python -c "\
import tomllib, subprocess, sys; \
deps = tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; \
subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--no-cache-dir'] + deps)" \
    && pip install --no-cache-dir uvicorn[standard]

# 可选：安装 Playwright Chromium + 系统依赖
RUN if [ "$INSTALL_PLAYWRIGHT" = "true" ]; then \
        playwright install --with-deps chromium; \
    fi

# 拷贝后端源码
COPY backend/agentpal/ ./agentpal/

# 拷贝前端构建产物
COPY --from=frontend-builder /app/frontend/dist ./static/

# 创建持久化目录
RUN mkdir -p /app/data /app/uploads /root/.nimo

ENV APP_ENV=production \
    DATABASE_URL="sqlite+aiosqlite:///./data/agentpal.db" \
    WORKSPACE_DIR="/root/.nimo"

EXPOSE 8088

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8088/health')" || exit 1

CMD ["uvicorn", "agentpal.main:app", "--host", "0.0.0.0", "--port", "8088"]
