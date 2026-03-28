<div align="center">
  <img src="frontend/public/nimo-logo.svg" width="200" alt="nimo logo" />
  <h1>nimo</h1>
  <p>🐠 基于 <a href="https://github.com/modelscope/agentscope">AgentScope</a> 构建的开源个人智能助手平台</p>

  [![CI](https://github.com/robscc/nimo/actions/workflows/ci.yml/badge.svg)](https://github.com/robscc/nimo/actions/workflows/ci.yml)
  [![codecov](https://codecov.io/gh/robscc/nimo/branch/main/graph/badge.svg)](https://codecov.io/gh/robscc/nimo)
  [![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
  [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
</div>

---

## ✨ 特性

- 🧠 **智能对话** — 基于 AgentScope 框架，支持多模型配置（Provider 管理），流式输出（SSE）
- 📱 **多渠道接入** — DingTalk（钉钉）、飞书（Feishu/Lark）、iMessage 开箱即用
- 🤖 **SubAgent 支持** — 多角色异步子代理，独立 Session 上下文，支持独立模型配置 + 运行时抽象
- 🛠️ **工具系统** — 内置 12 个工具（文件读写、Shell、浏览器、Python 执行、技能/定时任务 CLI 等），Tool Guard 安全防护，可视化开关管理 + 调用日志
- ⏰ **定时任务** — 标准 Cron 表达式调度，自动执行 + 结果通知 + CLI 管理
- 🔌 **技能扩展** — ZIP/URL 方式安装自定义技能包，版本管理 + 热重载
- 📊 **Dashboard** — 系统统计面板，汇总会话/消息/Token/工具/技能/任务等指标
- 🔔 **通知系统** — WebSocket 实时通知总线，全局系统事件推送
- 💻 **CLI 工具** — 命令行管理工具，支持进程管理、控制台输出
- 📦 **Task Artifacts** — SubAgent 产出物管理，支持文件/文本/代码等类型
- 🌐 **前后端分离** — React 前端 + FastAPI 后端，提供可视化管理界面
- 🧪 **测试驱动** — 672+ 单元与集成测试，E2E 覆盖核心流程

## 🏗️ 架构概览

```
┌──────────────────────────────────────────────────────────────┐
│                        消息渠道层                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐     │
│  │   Web    │  │ DingTalk │  │  Feishu  │  │ iMessage │     │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘     │
└───────┼──────────────┼─────────────┼─────────────┼───────────┘
        │              │             │             │
┌───────▼──────────────▼─────────────▼─────────────▼───────────┐
│                     FastAPI Backend                            │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │                  SchedulerClient                         │ │
│  │  (FastAPI 进程内客户端，连接独立 Scheduler 进程)          │ │
│  └────────────────────────┬────────────────────────────────┘ │
│                           │ ZMQ (DEALER ↔ ROUTER)            │
│  ┌────────────────────────▼────────────────────────────────┐ │
│  │              Scheduler 进程 (SchedulerBroker)            │ │
│  │  ┌──────────────────────────────────────────────────┐   │ │
│  │  │  ROUTER (请求路由) + XPUB/XSUB (事件代理)        │   │ │
│  │  └──────────┬──────────────┬──────────────┬─────────┘   │ │
│  │             │              │              │              │ │
│  │  ┌──────────▼───┐  ┌──────▼───────┐  ┌──▼──────────┐   │ │
│  │  │  PA Worker   │  │  Sub Worker  │  │ Cron Worker │   │ │
│  │  │  (per session)│  │  (per task)  │  │ (singleton) │   │ │
│  │  │  pa:{sid}    │  │  sub:{n}:{t} │  │ cron:sched  │   │ │
│  │  └──────────────┘  └──────────────┘  └─────────────┘   │ │
│  └─────────────────────────────────────────────────────────┘ │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐      │
│  │ Provider Mgr │  │ Tool System  │  │ Tool Guard    │      │
│  └──────────────┘  └──────────────┘  └───────────────┘      │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐      │
│  │ Session Mgr  │  │ Skill Mgr   │  │ Notification  │      │
│  └──────────────┘  └──────────────┘  │ Bus (WS)      │      │
│  ┌──────────────┐  ┌──────────────┐  └───────────────┘      │
│  │ Workspace    │  │ Memory       │                          │
│  │ Manager      │  │ (Hybrid)     │                          │
│  └──────────────┘  └──────────────┘                          │
│                         SQLite (WAL)                          │
└──────────────────────────────────────────────────────────────┘
        │
┌───────▼──────────────────────────────────────────────────────┐
│          React Frontend (Vite + TypeScript + Tailwind)        │
│  Chat · Sessions · Tools · Skills · Tasks · Cron ·           │
│  Workspace · Dashboard                                        │
└──────────────────────────────────────────────────────────────┘
```

### 多进程 Scheduler 架构

系统采用 **多进程 + ZMQ 消息总线** 架构，每个 Agent 运行在独立的 OS 进程中：

| 组件 | 进程模型 | 说明 |
|------|---------|------|
| **SchedulerClient** | FastAPI 主进程内 | 薄客户端，通过 ZMQ DEALER 连接 Scheduler |
| **SchedulerBroker** | 独立进程 | 中央调度器，管理所有 Worker 进程的生命周期 |
| **PA Worker** | 每 Session 一个进程 | 运行 `PersonalAssistantDaemon`，处理用户对话 |
| **Sub Worker** | 每 Task 一个进程 | 运行 `SubAgentDaemon`，执行委派任务 |
| **Cron Worker** | 全局单例进程 | 运行 `CronDaemon`，调度和执行定时任务 |

ZMQ 通信模型：
- **ROUTER/DEALER** — 请求/控制路由（`Envelope` + `msgpack` 序列化）
- **XPUB/XSUB** — 事件发布/订阅代理（SSE 流式推送）
- Worker 生命周期：`PENDING → STARTING → RUNNING → IDLE → STOPPING → STOPPED`

## 🚀 快速开始

### 前置条件

- Python 3.10+
- Node.js 18+
- Docker & Docker Compose（可选）

### 本地开发

```bash
# 克隆仓库
git clone https://github.com/robscc/nimo.git
cd nimo

# 一键启动开发环境
make dev

# 或者分别启动
make backend   # 后端 http://localhost:8088
make frontend  # 前端 http://localhost:3000
```

### Docker 部署

```bash
# 复制并配置环境变量
cp .env.example .env
# 编辑 .env 填写相关配置

# 启动所有服务
docker-compose up -d
```

## ⚙️ 配置

支持多种配置方式（优先级从高到低）：**环境变量 > `~/.nimo/config.yaml` > `.env` > 默认值**

推荐使用 `~/.nimo/config.yaml`：

```yaml
llm:
  provider: compatible      # 兼容 OpenAI 格式的 API
  model: qwen-plus
  api_key: your_api_key
  base_url: https://dashscope.aliyuncs.com/compatible-mode/v1

# 可选：消息渠道
dingtalk:
  app_key: ""
  app_secret: ""

feishu:
  app_id: ""
  app_secret: ""

imessage:
  enabled: false  # 仅 macOS
```

也可使用 `.env` 文件：

```env
LLM_MODEL=qwen-plus
LLM_API_KEY=your_api_key
```

## 📡 渠道配置

| 渠道 | 文档 | 状态 |
|------|------|------|
| DingTalk | [docs/channels/dingtalk.md](docs/channels/dingtalk.md) | ✅ 支持 |
| 飞书 Feishu | [docs/channels/feishu.md](docs/channels/feishu.md) | ✅ 支持 |
| iMessage | [docs/channels/imessage.md](docs/channels/imessage.md) | ✅ 支持（需 macOS）|

## 🔄 Agent 数据流

系统包含 3 种 Agent，各自运行在独立进程中，通过 ZMQ + MessageBus 协作：

### 1. PersonalAssistant (PA) — 用户对话

```
用户消息 (Web/DingTalk/飞书/iMessage)
    │
    ▼
FastAPI API → SchedulerClient → CHAT_REQUEST (ZMQ)
    │
    ▼
PA Worker (pa:{session_id})
    │
    ├─ 1. 存储用户消息到 Memory
    ├─ 2. 构建动态 System Prompt
    │     ├─ Workspace 上下文 (SOUL.md / AGENTS.md)
    │     ├─ SubAgent 花名册 (Registry 动态生成)
    │     ├─ 启用的工具列表 + Prompt Skills
    │     └─ @mention 路由提示 (如有)
    ├─ 3. 重建对话历史 (压缩感知，保留摘要)
    ├─ 4. 调用 LLM (最多 32 轮工具循环)
    │     ├─ 解析工具调用 (OpenAI 格式)
    │     ├─ Tool Guard 安全检查
    │     ├─ 执行工具 → 记录日志 → 追加到对话
    │     └─ 循环直到无工具调用
    ├─ 5. 流式输出 SSE 事件
    │     (thinking_delta / text_delta / tool_start / tool_done / done)
    ├─ 6. 存储助手回复 + 记录 Token 用量
    └─ 7. 触发记忆压缩 (超阈值时)
```

### 2. SubAgent — 任务委派

```
PA 调用 dispatch_sub_agent 工具 (或 @mention 触发)
    │
    ├─ 路由: agent_name 直接指定 (优先)
    │        或 task_type 匹配 Registry
    │
    ▼
SchedulerBroker 收到 DISPATCH_SUB → spawn 新进程
    │
    ▼
Sub Worker (sub:{agent_name}:{task_id})
    │
    ├─ 1. 加载 SubAgentTask + 角色定义
    ├─ 2. 创建独立上下文
    │     ├─ 独立 session_id: "sub:{parent}:{task_id}"
    │     ├─ 独立 BufferMemory (不污染主对话)
    │     └─ 独立模型配置 (未配置回退到全局)
    ├─ 3. 检查 MessageBus 待处理消息
    ├─ 4. 调用 LLM + 多轮工具循环
    │     ├─ 每轮检查 MessageBus 新消息
    │     ├─ 执行工具 → 记录 execution_log
    │     └─ 超出轮次 → 强制摘要
    ├─ 5. 发布 task_event (SSE 实时推送)
    ├─ 6. 更新 Task 状态 (DONE/FAILED)
    │     ├─ 写入 execution_log + result
    │     └─ 失败时: 指数退避重试
    └─ 7. 通知 PA
              │  AGENT_RESPONSE (ZMQ)
              ▼
         SchedulerBroker 拦截结果
              ├─ 写入父 Session 记忆
              └─ 发送 SSE 任务完成卡片
```

### 3. CronAgent — 定时任务

```
CronDaemon (cron:scheduler) 后台循环
    │  每 30 秒检查 get_due_jobs()
    ▼
发现到期任务
    │
    ├─ 1. 创建 CronJobExecution 记录 (RUNNING)
    ├─ 2. 实例化 CronAgent
    │     ├─ 轻量模式: 只加载 SOUL.md + AGENTS.md
    │     └─ 心跳模式: 加载完整 Workspace 上下文
    ├─ 3. 调用 LLM + 工具循环
    │     ├─ 执行工具 → 记录 execution_log
    │     └─ 返回最终结果
    ├─ 4. 更新执行记录 (DONE/FAILED)
    │     ├─ 写入 result + execution_log
    │     └─ 更新 next_run_at (croniter 计算)
    └─ 5. 通知 PA Session
          ├─ ZMQ AGENT_NOTIFY → pa:{session_id}
          └─ 发布 session 级 SSE 事件
```

### Agent 间通信 (MessageBus)

```
┌──────────┐   send()    ┌──────────────┐   ZMQ AGENT_NOTIFY   ┌──────────┐
│    PA    │────────────▶│  MessageBus  │──────────────────────▶│ SubAgent │
│          │◀────────────│  (DB + ZMQ)  │◀──────────────────────│          │
└──────────┘  receive()  └──────────────┘   AGENT_RESPONSE     └──────────┘
                               │
                               │  消息生命周期: PENDING → DELIVERED → PROCESSED
                               │  消息模式: request / response / notify / broadcast
```

## 🤖 SubAgent

SubAgent 是独立运行的异步子代理，拥有独立上下文、独立模型配置，支持多轮工具调用和 Agent 间通信。

### 运行机制

```
┌─────────────────────────────────────────────────────────────────┐
│                   PersonalAssistant (主 Agent)                    │
│                                                                   │
│  dispatch_sub_agent(task, agent_name/task_type, context)         │
│         │                                                         │
│         ▼                                                         │
│  ┌─────────────────┐    task_type     ┌────────────────────┐     │
│  │  SubAgentRegistry│◄───匹配────────│ SubAgentDefinition │     │
│  │  (角色注册中心)   │               │  · researcher       │     │
│  └────────┬────────┘               │  · coder             │     │
│           │                         │  · ops-engineer      │     │
│           ▼                         │  · 自定义角色...      │     │
│  创建 SubAgentTask (PENDING)        └────────────────────┘     │
│  生成独立 sub_session_id: "sub:<parent>:<task_id>"               │
│         │                                                         │
│         ▼  (asyncio 后台任务)                                    │
│  ┌──────────────────────────────────────────────────┐           │
│  │              SubAgent 实例                         │           │
│  │  · 独立 BufferMemory（不影响主上下文）             │           │
│  │  · 独立模型配置（可覆盖，未配置回退到主 Agent）    │           │
│  │  · 独立 AsyncSession（避免 DB 锁）                │           │
│  │                                                    │           │
│  │  run() → reply() 循环:                            │           │
│  │  ┌──────────────────────────────────────────┐    │           │
│  │  │ 1. 检查 MessageBus 待处理消息             │    │           │
│  │  │ 2. 构建消息列表 (system + history + user) │    │           │
│  │  │ 3. 调用 LLM                               │    │           │
│  │  │ 4. 解析工具调用 (OpenAI 格式)             │    │           │
│  │  │ 5. 执行工具 → 记录日志 → 追加到对话       │    │           │
│  │  │ 6. 循环直到无工具调用或达到 max_tool_rounds│    │           │
│  │  │ 7. 超出轮次 → 强制摘要 (force summary)    │    │           │
│  │  └──────────────────────────────────────────┘    │           │
│  │                                                    │           │
│  │  完成 → 更新 Task 状态 (DONE/FAILED)              │           │
│  │       → 写入 execution_log                        │           │
│  │       → 通过 MessageBus 通知主 Agent               │           │
│  │       → 发送 task_event_bus 实时事件               │           │
│  └──────────────────────────────────────────────────┘           │
└─────────────────────────────────────────────────────────────────┘
```

### 任务路由

SubAgent 支持两种路由方式：

| 方式 | 说明 | 优先级 |
|------|------|--------|
| `agent_name` | 直接指定 SubAgent 名称（如 `researcher`） | 🔴 最高 |
| `task_type` | 按任务类型匹配 SubAgent 的 `accepted_task_types` 列表 | 🟡 次之 |

```python
# 方式 1：直接指定 agent_name
task = await assistant.dispatch_sub_agent(
    task="分析报告并生成摘要",
    agent_name="researcher",
    context={"file": "report.pdf"},
    session_id="user_session_123",
)

# 方式 2：按 task_type 自动路由
task = await assistant.dispatch_sub_agent(
    task="写一个排序算法",
    task_type="coding",
    session_id="user_session_123",
)

# 查询任务状态
status = await assistant.get_task_status(task.id)
```

### 独立上下文

每个 SubAgent 实例拥有完全隔离的运行环境：

- **独立 Session**：`sub_session_id = "sub:<parent_session>:<task_id>"`，不与主对话混淆
- **独立 Memory**：使用独立的 `BufferMemory`，多轮工具对话不污染主上下文
- **独立 DB Session**：使用独立的 `AsyncSessionLocal`，避免与请求级 Session 的 SQLite 锁冲突

### 模型配置回退

SubAgent 可配置独立的模型参数，未配置的字段自动回退到主 Agent 的全局配置：

```
SubAgentDefinition.get_model_config(fallback=主Agent配置)
  → model_name:     SubAgent自定义 ?? 主Agent的 model
  → model_provider: SubAgent自定义 ?? 主Agent的 provider
  → api_key:        SubAgent自定义 ?? 主Agent的 api_key
  → base_url:       SubAgent自定义 ?? 主Agent的 base_url
```

### 执行日志

SubAgent 的每次运行都会记录完整的 `execution_log`，包含：

- `system_prompt` — 系统提示词
- `user_message` — 用户/主 Agent 下发的任务
- `llm_response` — 每轮 LLM 响应
- `tool_start` / `tool_done` — 工具调用开始/结束（含耗时 `duration_ms`）
- `forced_summary` — 超出工具轮次后的强制摘要
- `final_result` — 最终结果

日志通过 `task_event_bus` 实时推送（供 SSE 订阅），并持久化到 `SubAgentTask.execution_log` 字段。

### Agent 间通信

通过 `MessageBus` 实现异步消息传递：

| 消息模式 | 说明 |
|---------|------|
| `request` | 向其他 Agent 发送请求并等待响应 |
| `response` | 回复另一个 Agent 的请求 |
| `notify` | 单向通知（如任务完成通知主 Agent） |
| `broadcast` | 广播消息给所有相关 Agent |

SubAgent 在每轮工具调用前检查 `MessageBus` 中的待处理消息，并将其注入当前对话上下文。

### 内置角色

| 角色 | 说明 | 默认任务类型 |
|------|------|-------------|
| `researcher` | 研究与信息收集 | `research`, `summarize`, `analyze`, `report`, `investigate`, `compare` |
| `coder` | 编码与技术实现 | `code`, `debug`, `script`, `implement`, `test`, `refactor`, `fix` |
| `ops-engineer` | 运维与基础设施 | `ops`, `mysql`, `redis`, `kubernetes`, `k8s`, `logs`, `prometheus`, `monitoring`, `debug-infra`, `sre`, `devops` |

支持通过 API（`POST /api/v1/sub-agents`）或前端自定义添加新角色。

## 🛠️ 工具系统

内置 12 个工具，通过前端 `/tools` 页面可视化管理：

| 工具 | 说明 | 默认状态 |
|------|------|------|
| `get_current_time` | 获取当前时间 | ✅ 开启 |
| `read_file` | 读取文件内容 | ✅ 开启 |
| `browser_use` | 浏览器自动化 | ✅ 开启 |
| `send_file_to_user` | 发送文件给用户 | ✅ 开启 |
| `skill_cli` | 技能管理 CLI | ✅ 开启 |
| `cron_cli` | 定时任务管理 CLI | ✅ 开启 |
| `dispatch_sub_agent` | 派遣 SubAgent | ✅ 开启 |
| `write_file` | 写入文件 | ❌ 关闭 |
| `edit_file` | 编辑文件 | ❌ 关闭 |
| `execute_shell_command` | 执行 Shell 命令 | ❌ 关闭 |
| `execute_python_code` | 执行 Python 代码 | ❌ 关闭 |
| `produce_artifact` | 生成任务产出物 | ❌ 关闭 |

支持 **Tool Guard** 安全防护机制，对危险操作进行拦截和确认。

## ⏰ 定时任务

使用标准 5 段 Cron 表达式创建自动化任务：

```
# 每天早上 9 点发送日报
0 9 * * *

# 每周一提醒待办
0 9 * * 1
```

任务执行后通过消息总线向主 Agent 发送通知，执行日志完整记录。

## 🧪 测试

```bash
# 运行单元 + 集成测试（672 tests）
make test

# 仅单元测试
make test-unit

# 仅集成测试
make test-integration

# E2E 测试（需前后端运行）
cd backend && .venv/bin/pytest tests/e2e/ -v

# 覆盖率报告
make coverage
```

## 📁 项目结构

```
nimo/
├── .github/              # GitHub Actions & 模板
├── backend/              # FastAPI 后端
│   ├── agentpal/
│   │   ├── agents/       # PersonalAssistant、SubAgent、CronAgent、BaseAgent
│   │   ├── channels/     # DingTalk、Feishu、iMessage
│   │   ├── api/v1/       # REST API 路由（14 个 endpoint 模块）
│   │   ├── memory/       # 记忆模块（Buffer / SQLite / Hybrid / ReMeLight）
│   │   ├── models/       # SQLAlchemy ORM 模型（13 张表）
│   │   ├── providers/    # 模型提供方管理（Provider Manager、重试模型）
│   │   ├── runtimes/     # SubAgent 运行时抽象（Internal / HTTP）
│   │   ├── scheduler/    # 多进程 Scheduler（Broker / Client / Worker / Process）
│   │   ├── zmq_bus/      # ZMQ 消息总线（守护进程、协议、事件订阅）
│   │   ├── cli/          # 命令行工具（start / stop / restart / status / config）
│   │   ├── workspace/    # 工作空间管理（上下文构建、记忆写入）
│   │   ├── tools/        # 工具注册与内置工具（12 个）
│   │   └── services/     # 配置、Cron 调度、通知总线、事件总线
│   └── tests/            # unit / integration / e2e
├── frontend/             # React + Vite + TypeScript + Tailwind
│   └── src/
│       ├── pages/        # Chat、Tools、Skills、Tasks、Sessions、Workspace、Cron、Dashboard
│       ├── components/   # Layout、SessionPanel、MentionPopup、TaskArtifactViewer、NimoLogo
│       └── hooks/        # useTools、useSessions、useSkills、useCron、useTasks、useNotifications ...
├── docs/                 # 项目文档
└── docker-compose.yml
```

## 🤝 贡献

欢迎 PR 和 Issue！请先阅读 [CONTRIBUTING.md](.github/CONTRIBUTING.md)。

## 📄 License

[MIT](LICENSE)
