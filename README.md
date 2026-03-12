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

- 🧠 **智能对话** — 基于 AgentScope 框架，支持多模型配置
- 📱 **多渠道接入** — DingTalk（钉钉）、飞书（Feishu/Lark）、iMessage 开箱即用
- 🤖 **SubAgent 支持** — 异步子代理执行复杂任务，各自维护独立 Session
- 🌐 **前后端分离** — React 前端 + FastAPI 后端，提供可视化管理界面
- 🔌 **插件化渠道** — 标准化 Channel 接口，轻松扩展新的消息渠道
- 🧪 **测试驱动** — 完整的单元测试与集成测试覆盖

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
│  │         PersonalAssistant Agent        │              │
│  │  ┌─────────────────────────────────┐  │              │
│  │  │   SubAgent Pool (Async Tasks)   │  │              │
│  │  │  [SubAgent1] [SubAgent2] ...    │  │              │
│  │  └─────────────────────────────────┘  │              │
│  └────────────────────────────────────────┘              │
│  ┌──────────────┐  ┌───────────────────┐                 │
│  │ Session Mgr  │  │   Task Manager    │                 │
│  └──────────────┘  └───────────────────┘                 │
└──────────────────────────────────────────────────────────┘
        │
┌───────▼──────────────────────────────────────────────────┐
│              React Frontend (Dashboard)                    │
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
make backend   # 启动后端 (http://localhost:8088)
make frontend  # 启动前端 (http://localhost:3000)
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

复制 `.env.example` 到 `.env` 并填写：

```env
# LLM 配置
LLM_MODEL=qwen-max
LLM_API_KEY=your_api_key

# DingTalk
DINGTALK_APP_KEY=
DINGTALK_APP_SECRET=

# 飞书
FEISHU_APP_ID=
FEISHU_APP_SECRET=

# iMessage（仅 macOS）
IMESSAGE_ENABLED=true
```

详见 [配置文档](docs/configuration.md)。

## 📡 渠道配置

| 渠道 | 文档 | 状态 |
|------|------|------|
| DingTalk | [docs/channels/dingtalk.md](docs/channels/dingtalk.md) | ✅ 支持 |
| 飞书 Feishu | [docs/channels/feishu.md](docs/channels/feishu.md) | ✅ 支持 |
| iMessage | [docs/channels/imessage.md](docs/channels/imessage.md) | ✅ 支持（需 macOS）|

## 🤖 SubAgent

SubAgent 支持异步执行长时任务，每个 SubAgent 有独立的 Session 上下文：

```python
from agentpal.agents import PersonalAssistant

assistant = PersonalAssistant(config)

# 启动一个异步子任务
task = await assistant.dispatch_sub_agent(
    task="帮我分析这份报告并生成摘要",
    context={"file": "report.pdf"},
    session_id="user_session_123",
)

# 查询任务状态
status = await assistant.get_task_status(task.id)
```

详见 [SubAgent 设计文档](docs/sub_agent.md)。

## 🧪 测试

```bash
# 运行所有测试
make test

# 运行单元测试
make test-unit

# 运行集成测试
make test-integration

# 生成覆盖率报告
make coverage
```

## 📁 项目结构

```
nimo/
├── .github/              # GitHub Actions & 模板
├── backend/              # FastAPI 后端
│   ├── agentpal/        # 核心包
│   │   ├── agents/      # Agent 实现
│   │   ├── channels/    # 消息渠道
│   │   ├── api/         # REST API
│   │   ├── models/      # 数据模型
│   │   └── services/    # 业务服务
│   └── tests/           # 测试套件
├── frontend/             # React 前端
├── docs/                 # 项目文档
├── scripts/              # 工具脚本
└── docker-compose.yml
```

## 🤝 贡献

欢迎 PR 和 Issue！请先阅读 [CONTRIBUTING.md](.github/CONTRIBUTING.md)。

## 📄 License

[MIT](LICENSE)
