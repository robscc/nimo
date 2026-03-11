# Contributing to AgentPal

欢迎贡献！请遵循以下流程：

## 开发流程

1. Fork 仓库
2. 创建功能分支：`git checkout -b feat/my-feature`
3. 开发并编写测试
4. 确保 `make test` 和 `make lint` 通过
5. 提交 PR

## 代码规范

- **格式化**：`make format`（Ruff）
- **Lint**：`make lint`
- **测试覆盖率**：新代码应有 ≥ 80% 覆盖率

## 提交信息规范（Conventional Commits）

```
feat: 添加飞书渠道支持
fix: 修复 HybridMemory 预热重复消息问题
docs: 更新 README 安装步骤
test: 增加 SubAgent 状态流转测试
refactor: 重构 MemoryFactory 工厂方法
```

## 添加新的记忆后端

1. 在 `backend/agentpal/memory/` 创建新文件，继承 `BaseMemory`
2. 实现 `add()`、`get_recent()`、`clear()` 三个必要方法
3. 在 `MemoryFactory._REGISTRY` 中注册
4. 添加对应测试

## 添加新的渠道

1. 在 `backend/agentpal/channels/` 创建新文件，继承 `BaseChannel`
2. 实现 `parse_incoming()` 和 `send()` 两个方法
3. 在 `backend/agentpal/api/v1/endpoints/channel.py` 注册路由
4. 添加对应测试
