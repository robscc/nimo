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

- 🧠 **智能对话** — 基于 AgentScope 框架，支持多模型配置，流式输出（SSE）
- 📱 **多渠道接入** — DingTalk（钉钉）、飞书（Feishu/Lark）、iMessage 开箱即用
- 🤖 **SubAgent 支持** — 多角色异步子代理，独立 Session 上下文，支持独立模型配置
- 🛠️ **工具系统** — 内置 6 个工具（文件读写、Shell、浏览器、时间），可视化开关管理 + 调用日志
- ⏰ **定时任务** — 标准 Cron 表达式调度，自动执行 + 结果通知
- 🔌 **技能扩展** — ZIP/URL 方式安装自定义技能包
- 🌐 **前后端分离** — React 前端 + FastAPI 后端，提供可视化管理界面
- 🧪 **测试驱动** — 195+ 单元与集成测试，E2E 覆盖核心流程

## 🏗️ 架构概览

```
┌──────────────────────────────────────────────────────────┐
│                     消息渠道层                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐               │
│  │ DingTalk │  │  Feishu  │  │ iMessage │  ...           │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘               │
└───────┼──────────────┼─────────────┼────────────────────-┘
        │              │             │
┌───────▼──────────────▼─────────────▼────────────────────┐
│                    FastAPI Backend                         │
│  ┌────────────────────────────────────────┐              │
│  │       PersonalAssistant Agent          │              │
│  │  ┌─────────────────────────────────┐  │              │
│  │  │   SubAgent Pool (Async Tasks)   │  │              │
│  │  │  [SubAgent1] [SubAgent2] ...    │  │              │
│  │  └─────────────────────────────────┘  │              │
│  └────────────────────────────────────────┘              │
│  ┌──────────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ Session Mgr  │  │ Tool Sys │  │ Cron Scheduler   │   │
│  └──────────────┘  └──────────┘  └──────────────────┘   │
│                         SQLite (WAL)                      │
└──────────────────────────────────────────────────────────┘
        │
┌───────▼──────────────────────────────────────────────────┐
│          React Frontend (Dashboard)                        │
│  Chat · Sessions · Tools · Skills · Tasks · Workspace     │
└──────────────────────────────────────────────────────────┘
```

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
│           │                         │  · 自定义角色...      │     │
│           ▼                         └────────────────────┘     │
│  创建 SubAgentTask (PENDING)                                     │
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
| `researcher` | 研究与信息收集 | `research`, `analysis` |
| `coder` | 编码与技术实现 | `coding`, `development` |

支持通过 API（`POST /api/v1/sub-agents`）或前端自定义添加新角色。

## 🛠️ 工具系统

内置 6 个工具，通过前端 `/tools` 页面可视化管理：

| 工具 | 说明 | 默认状态 |
|------|------|------|
| `get_current_time` | 获取当前时间 | ✅ 开启 |
| `read_file` | 读取文件内容 | ✅ 开启 |
| `browser_use` | 浏览器自动化 | ✅ 开启 |
| `write_file` | 写入文件 | ❌ 关闭 |
| `edit_file` | 编辑文件 | ❌ 关闭 |
| `execute_shell_command` | 执行 Shell 命令 | ❌ 关闭 |

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
# 运行单元 + 集成测试（195 tests）
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
│   │   ├── agents/       # PersonalAssistant、SubAgent、CronAgent
│   │   ├── channels/     # DingTalk、Feishu、iMessage
│   │   ├── api/v1/       # REST API 路由
│   │   ├── memory/       # 记忆模块（Buffer / SQLite / Hybrid）
│   │   ├── models/       # SQLAlchemy ORM 模型
│   │   ├── tools/        # 工具注册与内置工具
│   │   └── services/     # 配置服务、Cron 调度器
│   └── tests/            # unit / integration / e2e
├── frontend/             # React + Vite + TypeScript + Tailwind
│   └── src/
│       ├── pages/        # Chat、Tools、Skills、Tasks、Sessions、Workspace
│       ├── components/   # Layout、SessionPanel
│       └── hooks/        # useTools、useSessions、useSkills、useCron ...
├── docs/                 # 项目文档
└── docker-compose.yml
```

## 🤝 贡献

欢迎 PR 和 Issue！请先阅读 [CONTRIBUTING.md](.github/CONTRIBUTING.md)。

## 📄 License

[MIT](LICENSE)
