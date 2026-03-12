.PHONY: help install dev backend frontend test test-unit test-integration coverage lint format clean docker-build docker-up docker-down

## 默认目标
help:
	@echo "AgentPal - Makefile 命令说明"
	@echo ""
	@echo "开发:"
	@echo "  make install       安装所有依赖"
	@echo "  make dev           启动完整开发环境"
	@echo "  make backend       仅启动后端服务"	@echo "  make frontend      仅启动前端服务"
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
	@echo "  make docker-build  构建 Docker 镜像"
	@echo "  make docker-up     启动 Docker 服务"
	@echo "  make docker-down   停止 Docker 服务"
	@echo ""
	@echo "  make clean         清理临时文件"

## ─── 安装依赖 ───────────────────────────────────────────────

install:
	@echo ">> 安装 Python 依赖..."
	cd backend && pip install -e ".[dev]"
	@echo ">> 安装前端依赖..."
	cd frontend && npm install
	@echo ">> 安装完成 ✅"

## ─── 开发服务 ───────────────────────────────────────────────

dev:
	@echo ">> 启动开发环境 (backend + frontend)..."
	make -j2 backend frontend

backend:
	@echo ">> 启动后端服务 (http://localhost:8099)..."
	cd backend && .venv/bin/python -m uvicorn agentpal.main:app \
		--reload --reload-dir agentpal \
		--host 0.0.0.0 --port 8099

frontend:
	@echo ">> 启动前端服务 (http://localhost:3000)..."
	cd frontend && npm run dev

## ─── 测试 ───────────────────────────────────────────────────

test:
	@echo ">> 运行全部测试..."
	cd backend && python -m pytest tests/ -v --tb=short

test-unit:
	@echo ">> 运行单元测试..."
	cd backend && python -m pytest tests/unit/ -v --tb=short

test-int:
	@echo ">> 运行集成测试..."
	cd backend && python -m pytest tests/integration/ -v --tb=short

coverage:
	@echo ">> 生成测试覆盖率报告..."
	cd backend && python -m pytest tests/ --cov=agentpal --cov-report=html --cov-report=term-missing
	@echo ">> 报告已生成: backend/htmlcov/index.html"

## ─── 代码质量 ───────────────────────────────────────────────

lint:
	@echo ">> 运行 Ruff 检查..."
	cd backend && ruff check agentpal/ tests/
	@echo ">> 运行 mypy 类型检查..."
	cd backend && mypy agentpal/ --ignore-missing-imports
	@echo ">> Lint 通过 ✅"

format:
	@echo ">> 格式化代码..."
	cd backend && ruff format agentpal/ tests/
	cd backend && ruff check --fix agentpal/ tests/
	@echo ">> 格式化完成 ✅"

## ─── Docker ─────────────────────────────────────────────────

docker-build:
	docker-compose build

docker-up:
	docker-compose up -d
	@echo ">> 服务已启动:"
	@echo "   后端: http://localhost:8088"
	@echo "   前端: http://localhost:3000"

docker-down:
	docker-compose down

## ─── 清理 ───────────────────────────────────────────────────

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	find . -name ".coverage" -delete 2>/dev/null || true
	@echo ">> 清理完成 ✅"
