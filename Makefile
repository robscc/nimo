.PHONY: help venv install dev backend frontend test test-unit test-integration coverage lint format clean \
       docker-build docker-build-amd64 docker-up docker-down docker-save docker-deploy

## ─── 变量 ─────────────────────────────────────────────────
# 可通过 make PYTHON=python3.13 覆盖
PYTHON ?= python3.12
VENV_DIR     := backend/.venv
VENV_BIN     := $(VENV_DIR)/bin
VENV_PYTHON  := $(VENV_BIN)/python
VENV_PIP     := $(VENV_BIN)/pip

# Docker
DOCKER       ?= podman
IMAGE_NAME   ?= nimo-agentpal
IMAGE_TAG    ?= latest
PLAYWRIGHT   ?= true
EXPORT_DIR   ?= /tmp

## 默认目标
help:
	@echo "AgentPal - Makefile 命令说明"
	@echo ""
	@echo "环境:"
	@echo "  make venv          创建 Python venv ($(PYTHON))"
	@echo "  make install       安装所有依赖 (venv + npm)"
	@echo "  make venv-clean    删除 venv"
	@echo ""
	@echo "开发:"
	@echo "  make dev           启动完整开发环境"
	@echo "  make backend       仅启动后端服务"
	@echo "  make frontend      仅启动前端服务"
	@echo ""
	@echo "测试:"
	@echo "  make test          运行全部测试"
	@echo "  make test-unit     运行单元测试"
	@echo "  make test-int      运行集成测试"
	@echo "  make coverage      生成覆盖率报告"
	@echo ""
	@echo "代码质量:"
	@echo "  make lint          运行 Ruff + mypy 检查"
	@echo "  make format        格式化代码 (Ruff + Black)"
	@echo ""
	@echo "Docker:"
	@echo "  make docker-build       构建本机架构镜像 (默认带 Playwright)"
	@echo "  make docker-build-amd64 交叉编译 linux/amd64 镜像"
	@echo "  make docker-up          启动 Docker 服务"
	@echo "  make docker-down        停止 Docker 服务"
	@echo "  make docker-save        导出 amd64 镜像为 tar"
	@echo "  make docker-deploy      构建 + 导出 amd64 镜像 (一步到位)"
	@echo ""
	@echo "  可选变量:"
	@echo "    PLAYWRIGHT=false      不安装 Playwright"
	@echo "    DOCKER=docker         使用 Docker 而非 Podman"
	@echo "    IMAGE_TAG=v1.0        自定义镜像 tag"
	@echo "    EXPORT_DIR=./dist     自定义导出目录"
	@echo ""
	@echo "  make clean         清理临时文件"

## ─── 虚拟环境 ─────────────────────────────────────────────

$(VENV_DIR):
	@echo ">> 创建 Python venv ($(PYTHON))..."
	$(PYTHON) -m venv $(VENV_DIR)
	$(VENV_PIP) install --upgrade pip
	@echo ">> venv 就绪: $(VENV_DIR) ✅"

venv: $(VENV_DIR)  ## 创建 venv（幂等）

venv-clean:  ## 删除 venv
	rm -rf $(VENV_DIR)
	@echo ">> venv 已删除 ✅"

## ─── 安装依赖 ───────────────────────────────────────────────

install: $(VENV_DIR)
	@echo ">> 安装 Python 依赖..."
	cd backend && $(abspath $(VENV_PYTHON)) -m pip install -e ".[dev]"
	@echo ">> 安装前端依赖..."
	cd frontend && npm install
	@echo ">> 安装完成 ✅"

## ─── 开发服务 ───────────────────────────────────────────────

dev:
	@echo ">> 启动开发环境 (backend:8099 + frontend:3000)，Ctrl+C 退出..."
	@trap 'echo ">> 正在清理进程..."; kill 0; sleep 0.5; pkill -9 -f "nimo/backend/.venv/bin/python.*multiprocessing" 2>/dev/null || true' INT TERM EXIT; \
		(cd backend && $(abspath $(VENV_PYTHON)) -m uvicorn agentpal.main:app \
			--reload --reload-dir agentpal \
			--host 0.0.0.0 --port 8099) & \
		(cd frontend && npm run dev) & \
		wait

backend: $(VENV_DIR)
	@echo ">> 启动后端服务 (http://localhost:8099)..."
	cd backend && $(abspath $(VENV_PYTHON)) -m uvicorn agentpal.main:app \
		--reload --reload-dir agentpal \
		--host 0.0.0.0 --port 8099

frontend:
	@echo ">> 启动前端服务 (http://localhost:3000)..."
	cd frontend && npm run dev

## ─── 测试 ───────────────────────────────────────────────────

test: $(VENV_DIR)
	@echo ">> 运行全部测试..."
	cd backend && $(abspath $(VENV_PYTHON)) -m pytest tests/ -v --tb=short

test-unit: $(VENV_DIR)
	@echo ">> 运行单元测试..."
	cd backend && $(abspath $(VENV_PYTHON)) -m pytest tests/unit/ -v --tb=short

test-int: $(VENV_DIR)
	@echo ">> 运行集成测试..."
	cd backend && $(abspath $(VENV_PYTHON)) -m pytest tests/integration/ -v --tb=short

coverage: $(VENV_DIR)
	@echo ">> 生成测试覆盖率报告..."
	cd backend && $(abspath $(VENV_PYTHON)) -m pytest tests/ --cov=agentpal --cov-report=html --cov-report=term-missing
	@echo ">> 报告已生成: backend/htmlcov/index.html"

## ─── 代码质量 ───────────────────────────────────────────────

lint: $(VENV_DIR)
	@echo ">> 运行 Ruff 检查..."
	cd backend && $(abspath $(VENV_BIN))/ruff check agentpal/ tests/
	@echo ">> 运行 mypy 类型检查..."
	cd backend && $(abspath $(VENV_BIN))/mypy agentpal/ --ignore-missing-imports
	@echo ">> Lint 通过 ✅"

format: $(VENV_DIR)
	@echo ">> 格式化代码..."
	cd backend && $(abspath $(VENV_BIN))/ruff format agentpal/ tests/
	cd backend && $(abspath $(VENV_BIN))/ruff check --fix agentpal/ tests/
	@echo ">> 格式化完成 ✅"

## ─── Docker ─────────────────────────────────────────────────

docker-build:  ## 构建本机架构镜像（默认带 Playwright）
	@echo ">> 构建本机架构镜像 (PLAYWRIGHT=$(PLAYWRIGHT))..."
	$(DOCKER) build \
		--build-arg INSTALL_PLAYWRIGHT=$(PLAYWRIGHT) \
		-t $(IMAGE_NAME):$(IMAGE_TAG) .
	@echo ">> 构建完成: $(IMAGE_NAME):$(IMAGE_TAG) ✅"

docker-build-amd64:  ## 交叉编译 linux/amd64 镜像（Mac ARM → 服务器）
	@echo ">> 交叉编译 linux/amd64 镜像 (PLAYWRIGHT=$(PLAYWRIGHT))..."
	$(DOCKER) build \
		--platform linux/amd64 \
		--build-arg INSTALL_PLAYWRIGHT=$(PLAYWRIGHT) \
		-t $(IMAGE_NAME):amd64 .
	@echo ">> 构建完成: $(IMAGE_NAME):amd64 ✅"

docker-save:  ## 导出 amd64 镜像为 tar 文件
	@echo ">> 导出镜像 $(IMAGE_NAME):amd64 → $(EXPORT_DIR)/$(IMAGE_NAME)-amd64.tar ..."
	$(DOCKER) save $(IMAGE_NAME):amd64 -o $(EXPORT_DIR)/$(IMAGE_NAME)-amd64.tar
	@ls -lh $(EXPORT_DIR)/$(IMAGE_NAME)-amd64.tar
	@echo ">> 导出完成 ✅"
	@echo ">> 部署命令:"
	@echo "   scp $(EXPORT_DIR)/$(IMAGE_NAME)-amd64.tar user@server:/tmp/"
	@echo "   ssh user@server 'docker load -i /tmp/$(IMAGE_NAME)-amd64.tar && docker-compose up -d'"

docker-deploy: docker-build-amd64 docker-save  ## 构建 + 导出 amd64 镜像（一步到位）

docker-up:  ## 启动容器
	$(DOCKER)-compose up -d
	@echo ">> 服务已启动: http://localhost:$${APP_PORT:-8088}"

docker-down:  ## 停止容器
	$(DOCKER)-compose down

## ─── 清理 ───────────────────────────────────────────────────

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name ".coverage" -delete 2>/dev/null || true
	@echo ">> 清理完成 ✅"
