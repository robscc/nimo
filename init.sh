#!/usr/bin/env bash
# =============================================================================
# init.sh — nimo (AgentPal) 开发环境初始化脚本
# 每个 Coding Agent 会话开始时执行，确保环境就绪。
# 幂等：可重复运行，不产生副作用。
# =============================================================================
set -e

PROJECT_ROOT="/Users/chenchuansong/workspace/dev/nimo"
cd "$PROJECT_ROOT"

echo "================================================================"
echo "  nimo (AgentPal) 环境初始化"
echo "  项目路径: $PROJECT_ROOT"
echo "================================================================"

# ── 1. 后端依赖 ────────────────────────────────────────────────────────────
echo ""
echo ">>> [1/3] 安装后端依赖..."
cd "$PROJECT_ROOT/backend"

if [ ! -d ".venv" ]; then
    echo "    未发现 .venv，使用系统 Python 安装..."
    pip install -e ".[dev]" -q
else
    echo "    发现 .venv，使用虚拟环境安装..."
    .venv/bin/pip install -e ".[dev]" -q
fi
echo "    ✓ 后端依赖安装完成"

# ── 2. 前端依赖 ────────────────────────────────────────────────────────────
echo ""
echo ">>> [2/3] 安装前端依赖..."
cd "$PROJECT_ROOT/frontend"
npm ci --silent 2>/dev/null || npm install --silent
echo "    ✓ 前端依赖安装完成"

# ── 3. 回归冒烟测试（验证环境可用）────────────────────────────────────────
echo ""
echo ">>> [3/3] 运行冒烟测试..."
cd "$PROJECT_ROOT/backend"

PYTEST_CMD="python -m pytest"
if [ -f ".venv/bin/pytest" ]; then
    PYTEST_CMD=".venv/bin/pytest"
fi

$PYTEST_CMD tests/unit -x -q --tb=short 2>&1 | tail -5

# ── 完成 ───────────────────────────────────────────────────────────────────
echo ""
echo "================================================================"
echo "  === 环境就绪 ==="
echo ""
echo "  启动后端:  cd backend && .venv/bin/python -m uvicorn agentpal.main:app --port 8099 --reload"
echo "  启动前端:  cd frontend && npm run dev"
echo "  运行测试:  cd backend && .venv/bin/pytest tests/unit/ tests/integration/ -v --tb=short"
echo "  E2E 测试:  cd backend && .venv/bin/pytest tests/e2e/ -v --tb=short"
echo "================================================================"
