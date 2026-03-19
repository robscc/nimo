# ── Stage 1: 构建前端 ──────────────────────────────────────
# $BUILDPLATFORM = 构建机架构（如 Mac ARM），原生运行 node/esbuild，
# 避免 QEMU 模拟 amd64 时 esbuild 崩溃。
# 前端产物是纯静态文件（HTML/JS/CSS），与 CPU 架构无关。
FROM --platform=$BUILDPLATFORM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# ── Stage 2: Python 运行时（后端 + 前端静态文件）──────────────
# $TARGETPLATFORM = 目标部署架构（如 linux/amd64）。
FROM python:3.11-slim

WORKDIR /app

# 安装 Python 依赖（利用 layer cache：先装依赖，再拷源码）
COPY backend/pyproject.toml ./
RUN python -c "\
import tomllib, subprocess, sys; \
deps = tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']; \
subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--no-cache-dir'] + deps)" \
    && pip install --no-cache-dir uvicorn[standard]

# 可选：安装 Playwright Chromium + 系统依赖（ARG 放在 pip 之后，避免缓存失效）
ARG INSTALL_PLAYWRIGHT=false
RUN if [ "$INSTALL_PLAYWRIGHT" = "true" ]; then \
        pip install --no-cache-dir playwright \
        && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
        && apt-get update \
        && apt-get install -y --no-install-recommends \
            libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
            libcups2 libdrm2 libxkbcommon0 libatspi2.0-0 libxcomposite1 \
            libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
            libcairo2 libasound2 libwayland-client0 \
            fonts-noto-cjk fonts-freefont-ttf vim \
        && rm -rf /var/lib/apt/lists/* \
        && playwright install chromium; \
    fi

# 拷贝后端源码
COPY backend/agentpal/ ./agentpal/

# 拷贝前端构建产物（架构无关的静态文件）
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
