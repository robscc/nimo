# CLAUDE.md — AgentPal (nimo)

> 每次 Claude Code 会话启动时自动读取本文件。

## 项目概览

**AgentPal**（代号 nimo）是基于 [AgentScope 1.x](https://github.com/modelscope/agentscope) 构建的开源个人智能助手平台。

- GitHub：https://github.com/robscc/nimo
- 技术栈：FastAPI（后端）+ React（前端）+ SQLite（存储）
- 目标：多渠道接入（Web / DingTalk / 飞书 / iMessage）、工具调用、SubAgent 异步任务

---

## 仓库结构

```
agentpal/                       ← 项目根目录（本地路径）
├── backend/                    ← FastAPI 后端
│   ├── agentpal/
│   │   ├── agents/             ← PersonalAssistant、SubAgent、BaseAgent
│   │   ├── api/v1/endpoints/   ← agent.py, tools.py, session.py, channel.py
│   │   ├── channels/           ← dingtalk.py, feishu.py, imessage.py
│   │   ├── memory/             ← base / buffer / sqlite / hybrid / factory
│   │   ├── models/             ← ORM: memory, session, tool
│   │   ├── tools/              ← builtin.py (6个工具), registry.py
│   │   ├── config.py           ← pydantic-settings, 读 .env
│   │   ├── database.py         ← async SQLAlchemy + init_db()
│   │   └── main.py             ← FastAPI app factory + lifespan
│   ├── tests/                  ← pytest 单元 + 集成测试
│   ├── .env                    ← 本地配置（不提交）
│   └── pyproject.toml          ← 包元数据、依赖、ruff/pytest 配置
├── frontend/                   ← React + Vite + TypeScript + Tailwind
│   └── src/
│       ├── pages/              ← ChatPage, ToolsPage, TasksPage
│       ├── components/         ← Layout (侧边栏导航)
│       ├── hooks/              ← useTools, useToggleTool, useToolLogs
│       └── api/index.ts        ← axios base client
├── .github/workflows/          ← ci.yml (lint + test matrix), release.yml
├── Makefile                    ← make dev / test / lint / format / docker-*
└── docker-compose.yml
```

---

## 开发环境启动

```bash
# 后端 (http://localhost:8099，--reload 热重载)
cd backend
.venv/bin/python -m uvicorn agentpal.main:app --port 8099 --reload

# 前端 (http://localhost:3000)
cd frontend
npm run dev
```

> **注意**：`Makefile` 里的端口是 8088，实际本地跑在 **8099**（避免与 CoPaw Console 冲突）。
> 前端 `vite.config.ts` 代理目标为 `http://localhost:8099`。

---

## 关键配置（backend/.env）

```env
LLM_PROVIDER=compatible
LLM_MODEL=gpt-4o-mini          # 当前使用 wlai.vip 兼容 API
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.wlai.vip/v1

DATABASE_URL=sqlite+aiosqlite:///./agentpal_dev.db
MEMORY_BACKEND=hybrid          # buffer | sqlite | hybrid
APP_PORT=8099
```

---

## 核心设计决策

### 1. LLM 调用（agentscope 1.x）
- 使用 `agentscope.model.OpenAIChatModel` 直接实例化，**不** 使用 `agentscope.init()`
- 工具调用需 **OpenAI 格式**（非 Anthropic format）：
  - 助手消息：`{"role": "assistant", "content": null, "tool_calls": [...]}`
  - 工具结果：`{"role": "tool", "tool_call_id": "...", "content": "..."}`
- `toolkit.call_tool_function(tool_call)` 是 coroutine，返回 AsyncGenerator：
  ```python
  async for chunk in await toolkit.call_tool_function(tool_call):
      tool_response = chunk
  ```

### 2. 流式输出（SSE）
- `POST /api/v1/agent/chat` 返回 `text/event-stream`
- 事件格式：
  ```
  data: {"type": "tool_start", "id": "...", "name": "...", "input": {...}}
  data: {"type": "tool_done",  "id": "...", "name": "...", "output": "...", "duration_ms": 3}
  data: {"type": "text_delta", "delta": "..."}
  data: {"type": "done"}
  data: {"type": "error",      "message": "..."}
  ```
- 前端用 `fetch` + `ReadableStream` 解析（不用 EventSource，因为需要 POST）

### 3. 记忆模块（可扩展）
- `BaseMemory` ABC → `BufferMemory` / `SQLiteMemory` / `HybridMemory`
- `HybridMemory`：BufferMemory 做热缓存，SQLiteMemory 做持久化，冷启动时 warm-up
- 新增记忆后端只需实现 `add / get_recent / clear` 三个方法

### 4. 工具系统
- `backend/agentpal/tools/builtin.py`：6 个内置工具
  - 安全（默认开启）：`read_file`, `browser_use`, `get_current_time`
  - 危险（默认关闭）：`execute_shell_command`, `write_file`, `edit_file`
- `ToolConfig` 表持久化启用状态；`ToolCallLog` 表记录每次调用
- 前端 `/tools` 页面：Toggle 开关 + 调用日志 accordion

### 5. 数据库
- 仅使用 SQLite（`aiosqlite` + `sqlalchemy 2.x async`）
- 表：`memory_records`, `sessions`, `sub_agent_tasks`, `tool_configs`, `tool_call_logs`
- `init_db()` 在 lifespan 里调用，idempotent

---

## API 路由

```
POST   /api/v1/agent/chat              ← 流式对话（SSE）
POST   /api/v1/agent/dispatch          ← 派遣 SubAgent
GET    /api/v1/agent/tasks/{task_id}   ← 查询 SubAgent 状态
GET    /api/v1/sessions/{id}           ← 会话信息
DELETE /api/v1/sessions/{id}/memory    ← 清空记忆
GET    /api/v1/tools                   ← 工具列表（含启用状态）
PATCH  /api/v1/tools/{name}            ← 启用/禁用工具
GET    /api/v1/tools/logs              ← 调用日志（可按 tool_name 筛选）
GET    /health
```

---

## 测试

```bash
cd backend
.venv/bin/pytest tests/ -v --tb=short   # 全部
.venv/bin/pytest tests/unit/            # 单元
.venv/bin/pytest tests/integration/     # 集成（需真实 DB）
```

CI 矩阵：Python 3.10 / 3.11 / 3.12，覆盖率要求 ≥ 75%。

---

## 常见坑 & 已知问题

| 问题 | 解决方案 |
|------|----------|
| `agentscope.agents` 不存在 | agentscope 1.x 用 `agentscope.agent`（单数），且直接用 model 类 |
| `OpenAIChatModel` 不接受 `base_url` 参数 | 通过 `client_kwargs={"base_url": ...}` 传入 |
| `toolkit.call_tool_function` 报 "not iterable" | 需要 `await` 后再 `async for`：`async for c in await toolkit.call_tool_function(...)` |
| 工具结果格式错误导致 API 500 | 必须用 OpenAI `role: tool` 格式，不能用 Anthropic `tool_result` 格式 |
| Vite proxy 缓冲 SSE | `vite.config.ts` 已加 `proxyRes` 事件设置 `x-accel-buffering: no` |
| API 提供商 503 | wlai.vip 偶发"无可用渠道"，换 `LLM_MODEL=gpt-4o-mini` 通常可用 |

---

## Git 提交风格

```
feat: 新功能
fix:  修复
chore: 构建/配置/依赖
refactor: 重构
test: 测试
docs: 文档
```

分支：直接提 `main`（个人项目）。
