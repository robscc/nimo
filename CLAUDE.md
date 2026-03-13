# CLAUDE.md — AgentPal (nimo)

> 每次 Claude Code 会话启动时自动读取本文件。

## 项目概览

**AgentPal**（代号 nimo）是基于 [AgentScope 1.x](https://github.com/modelscope/agentscope) 构建的开源个人智能助手平台。

- GitHub：https://github.com/robscc/nimo
- 技术栈：FastAPI（后端）+ React（前端）+ SQLite（存储）
- 目标：多渠道接入（Web / DingTalk / 飞书 / iMessage）、工具调用、SubAgent 多角色协作、定时任务

---

## 仓库结构

```
agentpal/                       ← 项目根目录（本地路径）
├── backend/                    ← FastAPI 后端
│   ├── agentpal/
│   │   ├── agents/             ← PersonalAssistant、SubAgent、CronAgent、BaseAgent
│   │   │   ├── personal_assistant.py  ← 主 Agent（流式对话 + 工具调用）
│   │   │   ├── sub_agent.py           ← SubAgent（独立上下文 + 多轮工具 + 执行日志）
│   │   │   ├── cron_agent.py          ← 定时任务 Agent（轻量，只加载 SOUL.md + AGENTS.md）
│   │   │   ├── registry.py            ← SubAgent 角色注册（CRUD + 任务路由）
│   │   │   └── message_bus.py         ← Agent 间异步消息总线
│   │   ├── api/v1/endpoints/   ← agent, tools, session, channel, sub_agents, cron, skills, workspace, config
│   │   ├── channels/           ← dingtalk.py, feishu.py, imessage.py
│   │   ├── memory/             ← base / buffer / sqlite / hybrid / factory
│   │   ├── models/             ← ORM: memory, session, tool, skill, agent, cron, message
│   │   ├── services/           ← config_file.py, cron_scheduler.py
│   │   ├── tools/              ← builtin.py (6个工具), registry.py
│   │   ├── config.py           ← pydantic-settings（优先级: env > ~/.nimo/config.yaml > .env > defaults）
│   │   ├── database.py         ← async SQLAlchemy + init_db()
│   │   └── main.py             ← FastAPI app factory + lifespan
│   ├── tests/
│   │   ├── unit/               ← 单元测试（agents, memory, channels, cron, config, skills, browser_use）
│   │   ├── integration/        ← 集成测试（API + 内存 SQLite）
│   │   └── e2e/                ← Playwright E2E 测试（需前后端运行）
│   └── pyproject.toml          ← 包元数据、依赖、ruff/pytest 配置
├── frontend/                   ← React + Vite + TypeScript + Tailwind
│   └── src/
│       ├── pages/              ← ChatPage, ToolsPage, SkillsPage, TasksPage, SessionsPage, WorkspacePage
│       ├── components/         ← Layout (侧边栏导航), SessionPanel, NimoIcon
│       ├── hooks/              ← useTools, useSessions, useSessionMeta, useSkills, useSubAgents, useCron
│       └── api/index.ts        ← axios base client + 全部 API 类型定义
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

## 关键配置

配置优先级（从高到低）：环境变量 > `~/.nimo/config.yaml` > `.env` > 代码默认值。

当前本地配置在 `~/.nimo/config.yaml`：

```yaml
llm:
  provider: compatible
  model: qwen3.5-plus
  api_key: sk-...
  base_url: https://coding.dashscope.aliyuncs.com/v1
```

> **注意**：创建 Session 时 `model_name` 会持久化到 DB。改全局配置只影响新 Session，旧 Session 需手动更新或新建。

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
  data: {"type": "thinking_delta", "delta": "..."}
  data: {"type": "tool_start", "id": "...", "name": "...", "input": {...}}
  data: {"type": "tool_done",  "id": "...", "name": "...", "output": "...", "duration_ms": 3}
  data: {"type": "text_delta", "delta": "..."}
  data: {"type": "file", "url": "...", "name": "...", "mime": "..."}
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
- Session 级工具/技能配置：可单独覆盖全局设置（null = 跟随全局）

### 5. SubAgent 系统
- **角色定义**：`SubAgentDefinition` 模型（`models/agent.py`），包含 name、role_prompt、accepted_task_types、独立模型配置
- **角色注册**：`SubAgentRegistry`（`agents/registry.py`），启动时自动创建默认角色（researcher、coder）
- **任务路由**：主 Agent 通过 `task_type` 匹配 SubAgent 的 `accepted_task_types`，或直接指定 `agent_name`
- **独立上下文**：每个 SubAgent 有独立的 session_id、BufferMemory，不影响主上下文
- **独立模型配置**：SubAgent 可配置自己的 model_name/provider/api_key/base_url，未配置则回退到全局
- **执行日志**：`SubAgentTask.execution_log` 记录完整的 LLM 对话 + 工具调用过程
- **Agent 间通信**：`MessageBus`（`agents/message_bus.py`）支持 request/response/notify/broadcast 消息模式

### 6. 定时任务系统（Cron）
- **调度器**：`CronScheduler`（`services/cron_scheduler.py`）— asyncio 后台任务，每 30 秒检查到期任务
- **Cron 表达式**：使用 `croniter` 库解析标准 5 段 cron 表达式
- **执行**：到期任务 spawn 异步 Task，创建轻量 `CronAgent`（只加载 SOUL.md + AGENTS.md）
- **结果通知**：执行完成后通过 `MessageBus` 向主 Agent 发送 NOTIFY 消息
- **执行日志**：`CronJobExecution` 记录状态、结果、完整 execution_log（LLM + 工具调用）
- **生命周期**：在 FastAPI lifespan 中 `await cron_scheduler.start()` / `stop()`

### 7. 数据库
- 仅使用 SQLite（`aiosqlite` + `sqlalchemy 2.x async`），WAL 模式
- 表：`memory_records`, `sessions`, `sub_agent_tasks`, `tool_configs`, `tool_call_logs`, `skills`, `sub_agent_definitions`, `cron_jobs`, `cron_job_executions`, `agent_messages`
- `init_db()` 在 lifespan 里调用，idempotent
- **重要**：Service 层用 `flush()`，API 层负责 `commit()`。工具执行前需 `commit()` 避免 SQLite 锁

---

## API 路由

```
# 对话
POST   /api/v1/agent/chat              ← 流式对话（SSE）
POST   /api/v1/agent/dispatch          ← 派遣 SubAgent
GET    /api/v1/agent/tasks/{task_id}   ← 查询 SubAgent 任务状态

# 会话
GET    /api/v1/sessions                ← 会话列表（含 model_name、channel）
POST   /api/v1/sessions                ← 创建会话
GET    /api/v1/sessions/{id}           ← 会话信息
GET    /api/v1/sessions/{id}/meta      ← 会话元信息（模型、工具、技能配置）
PATCH  /api/v1/sessions/{id}/config    ← 更新会话级配置
GET    /api/v1/sessions/{id}/messages  ← 历史消息
DELETE /api/v1/sessions/{id}           ← 删除会话（软删除）
DELETE /api/v1/sessions/{id}/memory    ← 清空记忆

# SubAgent
GET    /api/v1/sub-agents              ← SubAgent 列表
GET    /api/v1/sub-agents/{name}       ← 获取 SubAgent
POST   /api/v1/sub-agents              ← 创建 SubAgent
PATCH  /api/v1/sub-agents/{name}       ← 更新 SubAgent
DELETE /api/v1/sub-agents/{name}       ← 删除 SubAgent

# 定时任务
GET    /api/v1/cron                    ← 定时任务列表
POST   /api/v1/cron                    ← 创建定时任务
PATCH  /api/v1/cron/{id}              ← 更新定时任务
DELETE /api/v1/cron/{id}              ← 删除定时任务
PATCH  /api/v1/cron/{id}/toggle       ← 启用/禁用
GET    /api/v1/cron/{id}/executions   ← 执行记录
GET    /api/v1/cron/executions/{id}/detail ← 执行详情（含完整日志）

# 工具
GET    /api/v1/tools                   ← 工具列表（含启用状态）
PATCH  /api/v1/tools/{name}            ← 启用/禁用工具
GET    /api/v1/tools/logs              ← 调用日志

# 技能
GET    /api/v1/skills                  ← 技能列表
POST   /api/v1/skills/install/zip      ← ZIP 安装
POST   /api/v1/skills/install/url      ← URL 安装

# 全局配置
GET    /api/v1/config                  ← 获取配置
PUT    /api/v1/config                  ← 更新配置
POST   /api/v1/config/init             ← 初始化配置

# 工作空间
GET    /api/v1/workspace/info          ← 工作空间信息
GET    /api/v1/workspace/files         ← 文件列表

GET    /health
```

---

## 前端页面

| 路由 | 页面 | 功能 |
|------|------|------|
| `/chat` | ChatPage | 流式对话、工具调用展示、思考过程、会话信息面板（卡片式工具/技能切换） |
| `/sessions` | SessionsPage | 会话管理列表（搜索、模型/消息数/时间信息、展开详情、快捷跳转对话） |
| `/tools` | ToolsPage | 工具 Toggle 开关 + 调用日志 |
| `/skills` | SkillsPage | 技能安装（ZIP/URL）+ 已安装列表 |
| `/tasks` | TasksPage | SubAgent 任务状态 |
| `/workspace` | WorkspacePage | Agent 工作空间文件管理 |

---

## 测试

```bash
cd backend

# 单元 + 集成测试（195 tests）
.venv/bin/pytest tests/unit/ tests/integration/ -v --tb=short

# E2E 测试（21 tests，需前后端运行）— 包含 LLM 对话测试
.venv/bin/pytest tests/e2e/ -v --tb=short

# 注意：e2e 和 unit/integration 不要混合运行（Playwright 会污染 asyncio event loop）
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
| SQLite "database is locked" | 工具执行前需 `await self._db.commit()`，Service 层用 `flush()` API 层用 `commit()` |
| SQLAlchemy 保留字 `metadata` | ORM 模型字段不能叫 `metadata`，已改用 `extra` |
| Session 的 model_name 持久化 | 改全局配置不影响旧 Session，需新建 Session 或批量更新 DB |
| Playwright + pytest-asyncio event loop 冲突 | e2e 和 unit 测试不要混合运行，分开执行 |

---

## Git 工作流

**必须走 Feature Branch → PR，禁止直接提交 main。**

### 分支命名

```
feat/<描述>        新功能        feat/streaming-chat
fix/<描述>         修复          fix/tool-call-format
chore/<描述>       构建/配置     chore/update-deps
refactor/<描述>    重构          refactor/memory-module
test/<描述>        测试          test/add-tool-coverage
docs/<描述>        文档          docs/api-reference
```

### 标准流程

```bash
# 1. 从最新 main 拉分支
git checkout main && git pull origin main
git checkout -b feat/your-feature

# 2. 开发、提交（可多次）
git add <files>
git commit -m "feat: ..."

# 3. 推送并开 PR
git push -u origin feat/your-feature
gh pr create --title "feat: ..." --body "..."

# 4. PR 合并后清理
git checkout main && git pull origin main
git branch -d feat/your-feature
```

### Commit 消息格式

```
feat: 新功能
fix:  修复
chore: 构建/配置/依赖
refactor: 重构
test: 测试
docs: 文档
```
