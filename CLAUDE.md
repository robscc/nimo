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
│   │   │   ├── base.py               ← Agent 基类，共享功能
│   │   │   ├── registry.py            ← SubAgent 角色注册（CRUD + 任务路由）
│   │   │   └── message_bus.py         ← Agent 间异步消息总线（DB + ZMQ 混合）
│   │   ├── scheduler/           ← 多进程 Scheduler 架构（新）
│   │   │   ├── broker.py              ← SchedulerBroker — 中央调度器（进程管理 + 消息路由）
│   │   │   ├── client.py             ← SchedulerClient — FastAPI 进程内薄客户端
│   │   │   ├── worker.py             ← worker_main — Worker 子进程入口
│   │   │   ├── process.py            ← scheduler_process_main — Scheduler 进程入口
│   │   │   ├── config.py             ← SchedulerConfig — 地址/超时/策略配置
│   │   │   ├── state.py              ← AgentState / AgentProcessInfo — 进程状态机
│   │   │   └── scheduler.py          ← AgentScheduler — 过渡兼容层
│   │   ├── api/v1/endpoints/   ← agent, tools, session, channel, sub_agents, cron, skills, workspace, config, dashboard, memory, notifications, providers, tasks
│   │   ├── channels/           ← dingtalk.py, feishu.py, imessage.py
│   │   ├── memory/             ← base / buffer / sqlite / hybrid / factory / reme_light_adapter
│   │   ├── models/             ← ORM: memory, session, tool, skill, agent, cron, message, llm_usage
│   │   ├── providers/          ← Provider 管理（manager.py, provider.py, openai_provider.py, retry_model.py）
│   │   ├── runtimes/           ← SubAgent 运行时抽象（base.py, internal.py, http.py, registry.py）
│   │   ├── zmq_bus/            ← ZMQ 消息总线（manager.py, protocol.py, daemon.py, pa_daemon.py, sub_daemon.py, cron_daemon.py, event_subscriber.py）
│   │   ├── cli/                ← 命令行工具（app.py, commands/start|stop|restart|status）
│   │   ├── workspace/          ← 工作空间管理（manager.py, context_builder.py, defaults.py, memory_writer.py）
│   │   ├── services/           ← config_file.py, cron_scheduler.py, notification_bus.py, task_event_bus.py, session_event_bus.py, skill_event_bus.py
│   │   ├── tools/              ← builtin.py (12个工具), registry.py
│   │   ├── config.py           ← pydantic-settings（优先级: env > ~/.nimo/config.yaml > .env > defaults）
│   │   ├── database.py         ← async SQLAlchemy + init_db()
│   │   └── main.py             ← FastAPI app factory + lifespan
│   ├── tests/
│   │   ├── unit/               ← 单元测试（agents, memory, channels, cron, config, skills, browser_use, providers, cli, zmq, tool_guard, workspace, notifications）
│   │   ├── integration/        ← 集成测试（API + 内存 SQLite）
│   │   └── e2e/                ← Playwright E2E 测试（需前后端运行）
│   └── pyproject.toml          ← 包元数据、依赖、ruff/pytest 配置
├── frontend/                   ← React + Vite + TypeScript + Tailwind
│   └── src/
│       ├── pages/              ← ChatPage, ToolsPage, SkillsPage, TasksPage, SessionsPage, WorkspacePage, CronPage, DashboardPage
│       ├── components/         ← Layout (侧边栏导航), SessionPanel, NimoIcon, NimoLogo, MentionPopup, TaskArtifactViewer
│       ├── hooks/              ← useTools, useSessions, useSessionMeta, useSkills, useSubAgents, useCron, useTasks, useTaskArtifacts, useNotifications, useSessionEvents
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
- `ReMeLightAdapter`：当前主力适配器，支持跨 Session 记忆搜索
- `MemoryWriter`：记忆压缩与持久化写入
- 新增记忆后端只需实现 `add / get_recent / clear` 三个方法

### 4. 工具系统
- `backend/agentpal/tools/builtin.py`：12 个内置工具
  - 安全（默认开启）：`read_file`, `browser_use`, `get_current_time`, `send_file_to_user`, `skill_cli`, `cron_cli`, `dispatch_sub_agent`
  - 危险（默认关闭）：`execute_shell_command`, `write_file`, `edit_file`, `execute_python_code`, `produce_artifact`
- `ToolConfig` 表持久化启用状态；`ToolCallLog` 表记录每次调用
- 前端 `/tools` 页面：Toggle 开关 + 调用日志 accordion
- Session 级工具/技能配置：可单独覆盖全局设置（null = 跟随全局）
- **Tool Guard**：安全防护机制，对危险工具操作进行拦截和用户确认

### 5. SubAgent 系统
- **角色定义**：`SubAgentDefinition` 模型（`models/agent.py`），包含 name、role_prompt、accepted_task_types、独立模型配置
- **角色注册**：`SubAgentRegistry`（`agents/registry.py`），启动时自动创建默认角色（researcher、coder、ops-engineer）
- **任务路由**：主 Agent 通过 `task_type` 匹配 SubAgent 的 `accepted_task_types`，或直接指定 `agent_name`
- **独立上下文**：每个 SubAgent 有独立的 session_id、BufferMemory，不影响主上下文
- **独立模型配置**：SubAgent 可配置自己的 model_name/provider/api_key/base_url，未配置则回退到全局
- **执行日志**：`SubAgentTask.execution_log` 记录完整的 LLM 对话 + 工具调用过程
- **Agent 间通信**：`MessageBus`（`agents/message_bus.py`）支持 request/response/notify/broadcast 消息模式
- **Task Artifacts**：SubAgent 可通过 `produce_artifact` 工具生成产出物，持久化到 `task_artifacts` 表

### 6. 定时任务系统（Cron）
- **调度器**：`CronScheduler`（`services/cron_scheduler.py`）— asyncio 后台任务，每 30 秒检查到期任务
- **Cron 表达式**：使用 `croniter` 库解析标准 5 段 cron 表达式
- **执行**：到期任务 spawn 异步 Task，创建轻量 `CronAgent`（只加载 SOUL.md + AGENTS.md）
- **结果通知**：执行完成后通过 `MessageBus` 向主 Agent 发送 NOTIFY 消息
- **执行日志**：`CronJobExecution` 记录状态、结果、完整 execution_log（LLM + 工具调用）
- **生命周期**：在 FastAPI lifespan 中 `await cron_scheduler.start()` / `stop()`
- **CLI 管理**：通过 `cron_cli` 工具支持 list/create/update/delete/toggle/history

### 7. Provider 管理
- **Provider Manager**（`providers/manager.py`）：统一管理多个 LLM 提供方
- **Provider 抽象**（`providers/provider.py`）：定义 Provider 接口
- **OpenAI Provider**（`providers/openai_provider.py`）：兼容 OpenAI 格式的提供方实现
- **重试模型**（`providers/retry_model.py`）：带重试逻辑的模型包装器
- 支持通过 API 动态创建/删除/测试 Provider，获取可用模型列表

### 8. 运行时抽象（Runtimes）
- **Base Runtime**（`runtimes/base.py`）：运行时基类定义
- **Internal Runtime**（`runtimes/internal.py`）：进程内运行时，SubAgent 在同一进程中执行
- **HTTP Runtime**（`runtimes/http.py`）：远程 HTTP 运行时，SubAgent 通过 HTTP 调用执行
- **Runtime Registry**（`runtimes/registry.py`）：运行时注册与选择

### 9. ZMQ 消息总线
- **Manager**（`zmq_bus/manager.py`）：ZMQ 守护进程管理
- **Protocol**（`zmq_bus/protocol.py`）：消息协议定义
- **Daemon**（`zmq_bus/daemon.py`）：基础守护进程
- **PA Daemon**（`zmq_bus/pa_daemon.py`）：PersonalAssistant 守护进程
- **Sub Daemon**（`zmq_bus/sub_daemon.py`）：SubAgent 守护进程
- **Cron Daemon**（`zmq_bus/cron_daemon.py`）：Cron 守护进程
- **Event Subscriber**（`zmq_bus/event_subscriber.py`）：事件订阅器

### 10. 通知系统
- **Notification Bus**（`services/notification_bus.py`）：全局发布/订阅总线，支持 WebSocket 推送
- **Session Event Bus**（`services/session_event_bus.py`）：按 Session 的 SSE 事件推送
- **Task Event Bus**（`services/task_event_bus.py`）：按 Task 的 SSE 事件推送
- **Skill Event Bus**（`services/skill_event_bus.py`）：技能热重载/安装/回滚事件

### 11. 数据库
- 仅使用 SQLite（`aiosqlite` + `sqlalchemy 2.x async`），WAL 模式
- 表：`memory_records`, `sessions`, `sub_agent_tasks`, `task_artifacts`, `task_events`, `tool_configs`, `tool_call_logs`, `skills`, `sub_agent_definitions`, `cron_jobs`, `cron_job_executions`, `agent_messages`, `llm_call_logs`
- `init_db()` 在 lifespan 里调用，idempotent
- **重要**：Service 层用 `flush()`，API 层负责 `commit()`。工具执行前需 `commit()` 避免 SQLite 锁

### 12. 多进程 Scheduler 架构（新）
系统采用 **多进程 + ZMQ 消息总线** 架构，每个 Agent 运行在独立 OS 进程中：

- **SchedulerClient**（`scheduler/client.py`）：FastAPI 进程内薄客户端，通过 ZMQ DEALER 连接 Scheduler 进程
- **SchedulerBroker**（`scheduler/broker.py`）：独立进程中的中央调度器，管理所有 Worker 进程生命周期
  - 使用 `multiprocessing.get_context("spawn")` 创建子进程
  - ROUTER 路由请求，XPUB/XSUB 代理事件
  - 健康检查 + 空闲回收 + 自动重启
  - 拦截 SubAgent 结果写入父 Session 记忆
- **Worker**（`scheduler/worker.py`）：子进程入口，根据 `agent_type` 创建对应 Daemon
- **AgentState**（`scheduler/state.py`）：进程状态机 `PENDING → STARTING → RUNNING → IDLE → STOPPING → STOPPED → FAILED`
- **AgentProcessInfo**：每个 Worker 的元数据（PID、类型、状态、时间戳）

进程模型：
| Worker 类型 | 身份标识 | 生命周期 | Daemon 类 |
|------------|---------|---------|-----------|
| PA Worker | `pa:{session_id}` | 按 Session 按需创建，空闲回收 | `PersonalAssistantDaemon` |
| Sub Worker | `sub:{agent_name}:{task_id}` | 按 Task 创建，完成后回收 | `SubAgentDaemon` |
| Cron Worker | `cron:scheduler` | 全局单例，随 Scheduler 启动 | `CronDaemon` |

ZMQ 消息协议（`zmq_bus/protocol.py`）：
- `Envelope`：统一消息封装（`msg_id`, `msg_type`, `source`, `target`, `payload`），`msgpack` 序列化
- 控制消息：`ENSURE_PA`, `DISPATCH_SUB`, `SCHEDULER_SHUTDOWN`, `CONFIG_RELOAD`
- 生命周期：`AGENT_REGISTER`, `AGENT_HEARTBEAT`, `AGENT_SHUTDOWN`, `AGENT_STATE_CHANGE`
- 工作消息：`CHAT_REQUEST`, `DISPATCH_TASK`, `CRON_TRIGGER`
- Agent 通信：`AGENT_REQUEST`, `AGENT_RESPONSE`, `AGENT_NOTIFY`, `AGENT_BROADCAST`

---

## API 路由

```
# 对话
POST   /api/v1/agent/chat                        ← 流式对话（SSE）
POST   /api/v1/agent/dispatch                     ← 派遣 SubAgent
GET    /api/v1/agent/tasks                        ← 任务列表
GET    /api/v1/agent/tasks/{task_id}              ← 查询 SubAgent 任务状态
POST   /api/v1/agent/tool-guard/{request_id}/resolve  ← Tool Guard 确认

# 会话
GET    /api/v1/sessions                           ← 会话列表（含 model_name、channel）
POST   /api/v1/sessions                           ← 创建会话
GET    /api/v1/sessions/{id}                      ← 会话信息
GET    /api/v1/sessions/{id}/meta                 ← 会话元信息（模型、工具、技能配置）
PATCH  /api/v1/sessions/{id}/config               ← 更新会话级配置
GET    /api/v1/sessions/{id}/messages             ← 历史消息
DELETE /api/v1/sessions/{id}                      ← 删除会话（软删除）
DELETE /api/v1/sessions/{id}/memory               ← 清空记忆
GET    /api/v1/sessions/{id}/sub-tasks            ← 会话关联的子任务
GET    /api/v1/sessions/{id}/events               ← 会话 SSE 事件流
GET    /api/v1/sessions/{id}/usage                ← 会话 Token 用量

# SubAgent
GET    /api/v1/sub-agents                         ← SubAgent 列表
GET    /api/v1/sub-agents/{name}                  ← 获取 SubAgent
POST   /api/v1/sub-agents                         ← 创建 SubAgent
PATCH  /api/v1/sub-agents/{name}                  ← 更新 SubAgent
DELETE /api/v1/sub-agents/{name}                  ← 删除 SubAgent

# 任务
GET    /api/v1/tasks/{task_id}                    ← 任务详情
GET    /api/v1/tasks/{task_id}/events             ← 任务 SSE 事件流
GET    /api/v1/tasks/{task_id}/artifacts          ← 任务产出物列表
GET    /api/v1/tasks/{task_id}/artifacts/{id}     ← 产出物详情
POST   /api/v1/tasks/{task_id}/input              ← 恢复用户输入
POST   /api/v1/tasks/artifacts                    ← 创建产出物
POST   /api/v1/tasks/{task_id}/cancel             ← 取消任务

# 定时任务
GET    /api/v1/cron                               ← 定时任务列表
GET    /api/v1/cron/{id}                          ← 定时任务详情
POST   /api/v1/cron                               ← 创建定时任务
PATCH  /api/v1/cron/{id}                          ← 更新定时任务
DELETE /api/v1/cron/{id}                          ← 删除定时任务
PATCH  /api/v1/cron/{id}/toggle                   ← 启用/禁用
GET    /api/v1/cron/{id}/executions               ← 执行记录
GET    /api/v1/cron/executions/all                ← 所有执行记录
GET    /api/v1/cron/executions/{id}/detail        ← 执行详情（含完整日志）

# 工具
GET    /api/v1/tools                              ← 工具列表（含启用状态）
PATCH  /api/v1/tools/{name}                       ← 启用/禁用工具
GET    /api/v1/tools/logs                         ← 调用日志

# 技能
GET    /api/v1/skills                             ← 技能列表
GET    /api/v1/skills/events                      ← 技能 SSE 事件流
GET    /api/v1/skills/{name}                      ← 技能详情
GET    /api/v1/skills/{name}/versions             ← 技能版本列表
POST   /api/v1/skills/{name}/rollback             ← 技能版本回滚
POST   /api/v1/skills/install/zip                 ← ZIP 安装
POST   /api/v1/skills/install/url                 ← URL 安装
PATCH  /api/v1/skills/{name}                      ← 更新技能（启用/禁用）
DELETE /api/v1/skills/{name}                      ← 卸载技能

# Provider 管理
GET    /api/v1/providers                          ← Provider 列表
POST   /api/v1/providers                          ← 创建自定义 Provider
PATCH  /api/v1/providers/{id}                     ← 更新 Provider
DELETE /api/v1/providers/{id}                     ← 删除 Provider
POST   /api/v1/providers/{id}/test                ← 测试 Provider 连接
GET    /api/v1/providers/{id}/models              ← 获取可用模型列表
POST   /api/v1/providers/{id}/models/fetch        ← 刷新模型列表

# Dashboard
GET    /api/v1/dashboard/stats                    ← 系统统计数据

# 记忆搜索
POST   /api/v1/memory/search                     ← 跨 Session 记忆搜索
GET    /api/v1/memory/sessions/{id}/search        ← 单 Session 记忆搜索

# 通知
WS     /api/v1/notifications/ws                   ← WebSocket 实时通知

# 全局配置
GET    /api/v1/config                             ← 获取配置
PUT    /api/v1/config                             ← 更新配置
POST   /api/v1/config/init                        ← 初始化配置
POST   /api/v1/config/reload                      ← 重载配置

# 工作空间
GET    /api/v1/workspace/info                     ← 工作空间信息
GET    /api/v1/workspace/files                    ← 文件列表
GET    /api/v1/workspace/files/{name}             ← 读取文件
PUT    /api/v1/workspace/files/{name}             ← 写入文件
POST   /api/v1/workspace/memory                   ← 追加记忆
GET    /api/v1/workspace/memory/daily             ← 每日记忆日志
GET    /api/v1/workspace/memory/daily/list        ← 每日记忆列表
GET    /api/v1/workspace/canvas                   ← 画布文件列表
GET    /api/v1/workspace/canvas/{filename}        ← 读取画布文件
PUT    /api/v1/workspace/canvas/{filename}        ← 写入画布文件

# 渠道
POST   /api/v1/channels/dingtalk/webhook          ← 钉钉 Webhook
POST   /api/v1/channels/feishu/webhook            ← 飞书 Webhook

GET    /health
```

---

## Agent 数据流

系统包含 3 种 Agent 类型，各自运行在独立进程中，通过 ZMQ + MessageBus 协作。

### PA (PersonalAssistant) 对话流

```
用户消息 → FastAPI → SchedulerClient
    │  CHAT_REQUEST (ZMQ)
    ▼
PA Worker (pa:{session_id})
    │
    ├─ 1. _remember_user() → 存储到 Memory
    ├─ 2. _build_system_prompt() → 动态组装
    │     ├─ WorkspaceManager 上下文 (SOUL.md / AGENTS.md)
    │     ├─ SubAgentRegistry.build_roster_prompt() → 可用角色花名册
    │     ├─ _build_active_toolkit() → 启用工具列表 (Session 级覆盖)
    │     ├─ _load_prompt_skills() → Prompt 技能注入
    │     └─ _extract_mention_hint() → @mention 强制派遣指令
    ├─ 3. _get_history_with_meta(limit=20) → 压缩感知历史重建
    │     (过滤 compressed=true，保留 context_summary)
    ├─ 4. LLM 工具循环 (MAX_TOOL_ROUNDS=32)
    │     ├─ _build_model() → 实例化 OpenAIChatModel
    │     ├─ 解析 tool_calls (OpenAI 格式)
    │     ├─ ToolGuardManager 安全检查 (流式模式等待确认)
    │     ├─ _run_tool() → commit DB + 执行 + 记录日志
    │     └─ 追加 tool result → 继续循环
    ├─ 5. reply_stream() → SSE 事件流
    │     (thinking_delta / text_delta / tool_start / tool_done /
    │      tool_guard_request / tool_guard_resolved / file / done / error)
    ├─ 6. _remember_assistant() → 存储回复
    ├─ 7. _record_turn_usage() → LLMCallLog + Token 统计
    └─ 8. MemoryWriter.maybe_flush() → 记忆压缩 (超阈值时)
```

### SubAgent 任务委派流

```
PA 调用 dispatch_sub_agent 工具
    │  (或 @mention 触发 _extract_mention_hint → 强制派遣)
    │
    ├─ 路由: agent_name 直接指定 (优先级最高)
    │        或 task_type → SubAgentRegistry.find_agent_for_task()
    │
    ▼
PA → SchedulerBroker (DISPATCH_SUB) → spawn 新 Worker 进程
    │
    ▼
Sub Worker (sub:{agent_name}:{task_id})
    │
    ├─ 1. 加载 SubAgentTask + SubAgentDefinition
    ├─ 2. 创建隔离上下文
    │     ├─ session_id = "sub:{parent_session}:{task_id}"
    │     ├─ 独立 BufferMemory (不污染主对话)
    │     ├─ 独立 AsyncSession (避免 SQLite 锁)
    │     └─ 独立模型配置 (get_model_config + fallback)
    ├─ 3. _check_incoming_messages() → MessageBus 待处理消息
    ├─ 4. reply() → LLM 工具循环
    │     ├─ 每轮检查 MessageBus 新消息 → 注入上下文
    │     ├─ 执行工具 → _log() 记录 execution_log
    │     ├─ _emit_progress() → task_event_bus 实时推送
    │     └─ 超出轮次 → 强制 text-only 摘要调用
    ├─ 5. produce_artifact() → TaskArtifact 持久化 (可选)
    ├─ 6. _update_status(DONE/FAILED)
    │     ├─ 写入 execution_log + result 到 DB
    │     ├─ task_event_bus.emit() → SSE 推送
    │     └─ notification_bus → WebSocket 通知
    ├─ 7. 失败处理: retry_count < max → asyncio.create_task(_retry) 指数退避
    └─ 8. AGENT_RESPONSE → SchedulerBroker
              ├─ 写入父 Session Memory (任务完成摘要)
              └─ 发送 SSE 任务完成卡片到前端
```

### CronAgent 定时任务流

```
CronDaemon (cron:scheduler) — 全局单例 Worker
    │  后台循环: 每 30 秒 get_due_jobs()
    │  (也支持 API CRON_TRIGGER 手动触发)
    ▼
发现到期任务
    │
    ├─ 1. CronManager.create_execution() → CronJobExecution (RUNNING)
    ├─ 2. CronManager.mark_job_executed() → 更新 last_run_at + next_run_at
    ├─ 3. 实例化 CronAgent
    │     ├─ _build_cron_system_prompt()
    │     │   ├─ 轻量模式: SOUL.md + AGENTS.md (默认)
    │     │   └─ 心跳模式: full_context=True → ContextBuilder 完整上下文
    │     └─ _build_toolkit() → 启用工具
    ├─ 4. reply() → LLM 工具循环
    │     ├─ 执行工具 → _log() 记录到 execution_log
    │     └─ 返回最终结果文本
    ├─ 5. CronManager.finish_execution(DONE/FAILED)
    │     └─ 写入 result + execution_log + finished_at
    └─ 6. 通知 PA Session
          ├─ MessageBus.send(NOTIFY) → pa:{session_id}
          ├─ ZMQ AGENT_NOTIFY → PA Daemon
          └─ session_event_bus → SSE 推送到前端
```

### Agent 间通信 (MessageBus)

`MessageBus`（`agents/message_bus.py`）采用 **DB 持久化 + ZMQ 实时投递** 混合模式：

- **消息生命周期**：`PENDING → DELIVERED → PROCESSED`
- **消息模式**：`request`（请求等待响应）/ `response`（回复）/ `notify`（单向通知）/ `broadcast`（广播）
- **身份解析**：`pa:{session_id}` / `sub:{name}:{task_id}` / `cron:{name}` — 自动路由到对应 Daemon
- **DB 审计**：所有消息持久化到 `agent_messages` 表，ZMQ 仅用于实时投递加速

---

## 前端页面

| 路由 | 页面 | 功能 |
|------|------|------|
| `/chat` | ChatPage | 流式对话、工具调用展示、思考过程、会话信息面板（卡片式工具/技能切换） |
| `/sessions` | SessionsPage | 会话管理列表（搜索、模型/消息数/时间信息、展开详情、快捷跳转对话） |
| `/tools` | ToolsPage | 工具 Toggle 开关 + 调用日志 |
| `/skills` | SkillsPage | 技能安装（ZIP/URL）+ 已安装列表 + 版本管理 |
| `/tasks` | TasksPage | SubAgent 任务状态 + 产出物查看 |
| `/cron` | CronPage | 定时任务管理（创建/编辑/启停/执行记录） |
| `/workspace` | WorkspacePage | Agent 工作空间文件管理 + 画布 + 每日记忆 |
| `/dashboard` | DashboardPage | 系统统计面板（会话/消息/Token/工具/技能/任务汇总） |

---

## 测试

```bash
cd backend

# 单元 + 集成测试（672 tests）
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
