# AgentPal Case Bank

测试用例、使用场景和最佳实践集合。

## 目录结构

```
agentpal-casebank/
├── conversations/          # 对话测试用例
│   ├── basic/             # 基础对话
│   ├── tool-calling/      # 工具调用场景
│   └── multi-turn/        # 多轮对话
├── sub-agent-tasks/       # SubAgent 任务案例
│   ├── research/          # 研究类任务
│   ├── coding/            # 编码类任务
│   └── ops/               # 运维类任务
├── cron-jobs/             # 定时任务示例
├── skills/                # 技能测试用例
├── edge-cases/            # 边界情况和错误处理
└── performance/           # 性能测试场景
```

## 快速开始

### 1. 基础对话测试

```bash
# 启动后端
cd backend
.venv/bin/python -m uvicorn agentpal.main:app --port 8099 --reload

# 创建测试会话
curl -X POST http://localhost:8099/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"model_name": "qwen3.5-plus"}'

# 发送测试消息
curl -X POST http://localhost:8099/api/v1/agent/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "<session_id>",
    "message": "你好，请介绍一下你自己"
  }'
```

### 2. 工具调用测试

参考 `conversations/tool-calling/` 目录下的用例：

- `read_file.json` - 文件读取
- `execute_shell.json` - Shell 命令执行
- `browser_use.json` - 浏览器自动化
- `dispatch_sub_agent.json` - SubAgent 派遣

### 3. SubAgent 任务测试

```bash
# 派遣研究任务
curl -X POST http://localhost:8099/api/v1/agent/dispatch \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "<session_id>",
    "agent_name": "researcher",
    "task_description": "调研 FastAPI 的异步性能优化方案",
    "task_type": "research"
  }'

# 查询任务状态
curl http://localhost:8099/api/v1/agent/tasks/<task_id>
```

## 测试用例分类

### A. 对话场景

#### A1. 基础对话
- 问候和自我介绍
- 简单问答
- 上下文理解
- 多语言支持

#### A2. 工具调用
- 单工具调用
- 多工具链式调用
- 工具调用失败处理
- Tool Guard 确认流程

#### A3. 多轮对话
- 上下文保持
- 记忆检索
- 话题切换
- 长对话压缩

### B. SubAgent 任务

#### B1. 研究类 (researcher)
- 技术调研
- 文档分析
- 竞品对比
- 最佳实践总结

#### B2. 编码类 (coder)
- 功能实现
- Bug 修复
- 代码重构
- 单元测试编写

#### B3. 运维类 (ops-engineer)
- 日志分析
- 性能监控
- 故障排查
- 部署脚本

### C. 定时任务

#### C1. 监控类
- 系统健康检查
- 资源使用统计
- 错误日志扫描

#### C2. 报告类
- 每日摘要
- 周报生成
- 数据汇总

#### C3. 维护类
- 数据清理
- 备份任务
- 缓存刷新

### D. 边界情况

#### D1. 错误处理
- LLM API 超时
- 工具执行失败
- SQLite 锁冲突
- ZMQ 消息丢失

#### D2. 并发场景
- 多会话并发
- 同一会话多请求
- SubAgent 并发执行
- 定时任务重叠

#### D3. 资源限制
- 超长对话历史
- 大文件处理
- 高频工具调用
- 内存压力测试

## 性能基准

### 响应时间

| 场景 | P50 | P95 | P99 |
|------|-----|-----|-----|
| 简单问答 | 800ms | 1.5s | 2s |
| 单工具调用 | 2s | 4s | 6s |
| 多工具链式 | 5s | 10s | 15s |
| SubAgent 任务 | 10s | 30s | 60s |

### 并发能力

- 单实例支持 50+ 并发会话
- SubAgent 并发数受 CPU 核心数限制
- 建议配置：4 核 8GB 内存

### 资源消耗

- 空闲内存：~200MB
- 单会话内存：~50MB
- SubAgent 进程：~100MB
- SQLite 数据库：~10MB/1000 条消息

## 常见问题排查

### 1. 流式输出中断

**症状**：SSE 连接突然断开，前端收不到完整响应

**排查步骤**：
1. 检查 uvicorn 日志是否有异常
2. 查看 `~/.nimo/logs/worker-pa_*.log`
3. 确认 LLM API 是否超时
4. 检查工具执行是否抛出异常

### 2. SubAgent 任务卡住

**症状**：任务状态一直是 RUNNING，没有进展

**排查步骤**：
1. 查看 `~/.nimo/logs/worker-sub_*.log`
2. 检查 Scheduler 进程是否存活：`ps aux | grep scheduler`
3. 查看 ZMQ 消息是否正常投递
4. 确认 SQLite 是否有锁冲突

### 3. 定时任务不执行

**症状**：到期时间已过，任务没有触发

**排查步骤**：
1. 检查 `enabled` 字段是否为 true
2. 查看 `~/.nimo/logs/worker-cron_scheduler.log`
3. 确认 cron 表达式是否正确
4. 检查 Scheduler 进程是否正常运行

### 4. 工具调用失败

**症状**：工具返回错误或超时

**排查步骤**：
1. 查看 `tool_call_logs` 表的错误信息
2. 确认工具是否已启用
3. 检查 Tool Guard 是否拦截
4. 验证工具参数格式是否正确

## 贡献指南

### 添加新用例

1. 在对应目录下创建 JSON 文件
2. 包含以下字段：
   ```json
   {
     "name": "用例名称",
     "description": "用例描述",
     "category": "分类",
     "input": "输入内容",
     "expected_output": "期望输出",
     "tools_used": ["工具列表"],
     "notes": "注意事项"
   }
   ```
3. 提交 PR 并说明用例价值

### 报告问题

发现 Bug 或边界情况时：
1. 在 `edge-cases/` 下创建复现用例
2. 记录完整的错误日志
3. 提供环境信息（Python 版本、OS、依赖版本）
4. 提交 Issue 并关联用例文件

## 参考资料

- [API 文档](../backend/README.md)
- [架构设计](../CLAUDE.md)
- [测试指南](../backend/tests/README.md)
- [部署文档](../docs/deployment.md)

## 许可证

MIT License - 详见项目根目录 LICENSE 文件
