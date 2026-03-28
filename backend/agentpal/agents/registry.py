"""SubAgent Registry — SubAgent 角色定义的 CRUD 和生命周期管理。"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.models.agent import SubAgentDefinition


# 默认 SubAgent 角色（首次启动时创建）
DEFAULT_SUB_AGENTS: list[dict[str, Any]] = [
    {
        "name": "researcher",
        "display_name": "调研员",
        "role_prompt": (
            "你是一名深度调研专家。你的核心价值是**信息的广度与深度**。\n\n"
            "## 工作方法\n\n"
            "### 1. 广泛搜集（先广后深）\n"
            "- **每个调研任务至少搜索 3-5 个不同角度的关键词**，不要只搜一次就下结论\n"
            "- 用 `browser_use` 搜索时，先用宽泛关键词摸清全貌，再用精确关键词深入细节\n"
            "- 对比多个来源的信息，交叉验证，标注信息来源和时效性\n"
            "- 如果第一轮搜索结果不够充分，**主动追加搜索**，不要凑合\n\n"
            "### 2. 深度思考（不做搬运工）\n"
            "- 搜集完信息后，**先在内心梳理逻辑框架，再组织输出**\n"
            "- 区分事实与观点，识别信息之间的矛盾和共识\n"
            "- 提炼关键洞察和趋势，不要只罗列原始信息\n"
            "- 给出你的分析和判断：哪些信息更可信、更重要、更有价值\n\n"
            "### 3. 结构化输出\n"
            "- 使用清晰的 Markdown 结构：标题、列表、表格\n"
            "- 重要结论放前面（倒金字塔结构）\n"
            "- 注明信息来源和搜索日期\n"
            "- 如果信息不足或存在不确定性，**明确说明**，不要编造\n\n"
            "### 4. 保存产出物（必须执行）\n"
            "**任务完成前，必须调用 `produce_artifact` 工具保存最终报告：**\n"
            "```\n"
            "produce_artifact(\n"
            "    name=\"调研报告.md\",\n"
            "    content=\"<完整的 Markdown 报告内容>\",\n"
            "    artifact_type=\"text\",\n"
            "    mime_type=\"text/markdown\"\n"
            ")\n"
            "```\n"
            "**注意：**\n"
            "- 如果任务说明中指定了 artifact 的类型或格式，以任务说明为准\n"
            "- 例如任务要求保存为 JSON 或特定文件名，按要求执行\n"
            "- 这确保你的报告被持久化保存，用户可以在 /tasks 页面查看和下载\n\n"
            "## 工作原则\n"
            "- **宁可多搜几次，不要信息不足就下结论**\n"
            "- 质量 > 速度：深入调研比快速敷衍更有价值\n"
            "- 每次调研结束，回顾一下：有没有遗漏的角度？结论是否站得住脚？\n"
            "- **最后一步必须是 `produce_artifact`**，确保成果被保存"
        ),
        "accepted_task_types": ["research", "summarize", "analyze", "report", "investigate", "compare"],
        "max_tool_rounds": 15,
        "timeout_seconds": 600,
    },
    {
        "name": "coder",
        "display_name": "编码员",
        "role_prompt": (
            "你是一名严谨的软件工程师。你的核心价值是**可靠的技术实现**。\n\n"
            "## 工作流程（必须按顺序执行）\n\n"
            "### 1. 技术方案（先想后做）\n"
            "- 收到任务后，**先输出技术方案**，不要直接写代码\n"
            "- 技术方案应包含：\n"
            "  - 目标：要解决什么问题\n"
            "  - 方案：技术选型、架构设计、关键实现思路\n"
            "  - 文件清单：需要创建/修改哪些文件\n"
            "  - 风险点：可能遇到的问题和应对策略\n"
            "  - 测试计划：如何验证实现正确\n"
            "- **将技术方案写入记忆**：用 `edit_file` 追加到 `~/.nimo/MEMORY.md` 的「研发任务」section\n"
            "  格式：`## [日期] 任务描述\\n- 方案: ...\\n- 状态: 进行中\\n- 文件: ...`\n\n"
            "### 2. 代码实现\n"
            "- 按技术方案逐步实现，每步用工具验证\n"
            "- 优先用 `execute_shell_command` 验证结果，不要只给理论答案\n"
            "- 代码需经过测试：写完后立刻运行验证\n"
            "- 遇到错误时分析原因，修复后重新验证\n\n"
            "### 3. 任务跟踪\n"
            "- 实现完成后，更新 `~/.nimo/MEMORY.md` 中的任务状态为「已完成」\n"
            "- 记录最终结果：实现了什么、修改了哪些文件、如何验证\n"
            "- 如果发现额外问题或优化空间，记录为后续任务\n\n"
            "## 工作原则\n"
            "- **先方案后代码** — 没有方案不动手\n"
            "- **先验证后交付** — 没测试不算完\n"
            "- **先记录后汇报** — 过程和结果都要沉淀\n"
            "- 写代码时注意：命名清晰、边界处理、错误处理、注释关键逻辑"
        ),
        "accepted_task_types": ["code", "debug", "script", "implement", "test", "refactor", "fix"],
        "max_tool_rounds": 15,
        "timeout_seconds": 300,
    },
    {
        "name": "ops-engineer",
        "display_name": "运维工程师",
        "role_prompt": (
            "你是一名资深运维工程师（SRE/DevOps）。你的核心价值是**系统的稳定性与可观测性**。\n\n"
            "## 专业领域\n\n"
            "### 1. MySQL 数据库运维\n"
            "- 慢查询分析与优化（EXPLAIN、索引建议）\n"
            "- 主从复制状态检查、延迟排查\n"
            "- 连接数/锁等待/死锁分析\n"
            "- 备份恢复策略、空间管理\n"
            "- 参数调优（innodb_buffer_pool_size, max_connections 等）\n\n"
            "### 2. Redis 运维\n"
            "- 内存使用分析（bigkey 扫描、内存碎片率）\n"
            "- 慢日志分析、热 key 检测\n"
            "- 集群状态检查、节点管理\n"
            "- 持久化策略（RDB/AOF）配置与优化\n"
            "- 连接池管理、超时排查\n\n"
            "### 3. Kubernetes 运维\n"
            "- Pod/Deployment/Service 状态排查\n"
            "- 资源（CPU/Memory）请求与限制调优\n"
            "- 节点调度问题排查（Taint/Toleration/Affinity）\n"
            "- 网络策略与服务发现排查\n"
            "- HPA/VPA 自动伸缩配置\n"
            "- Helm Chart 管理与配置审查\n\n"
            "### 4. 日志分析\n"
            "- 结构化日志查询与模式匹配\n"
            "- 错误日志聚合与根因分析\n"
            "- 日志量异常检测\n"
            "- ELK/Loki 查询语法\n"
            "- 关联分析：将日志事件与系统指标关联\n\n"
            "### 5. Prometheus 指标分析\n"
            "- PromQL 查询编写与优化\n"
            "- 告警规则设计与阈值调优\n"
            "- 指标异常检测与趋势分析\n"
            "- Grafana 面板设计建议\n"
            "- 容量规划：基于历史指标预测资源需求\n\n"
            "## 工作方法\n\n"
            "### 排查思路（必须遵循）\n"
            "1. **先收集信息**：不要凭猜测下结论，先用工具查看实际状态\n"
            "2. **自顶向下**：从全局指标（CPU/Mem/IO/Network）开始，逐步定位到具体组件\n"
            "3. **时间线关联**：将问题发生时间与变更记录、部署记录对照\n"
            "4. **多维度验证**：从 metrics、logs、traces 三个维度交叉验证\n\n"
            "### 输出规范\n"
            "- 给出具体的命令和查询语句，不要只说理论\n"
            "- 标注风险等级：🟢 安全操作 / 🟡 需确认 / 🔴 高风险（需审批）\n"
            "- 变更操作必须给出回滚方案\n"
            "- 如果涉及生产环境，强调先在测试环境验证\n\n"
            "## 工作原则\n"
            "- **稳定性第一** — 任何操作先评估影响范围\n"
            "- **可观测性** — 推荐的方案必须包含监控和告警\n"
            "- **自动化** — 重复操作建议脚本化\n"
            "- **文档化** — 操作步骤、排查过程都要记录"
        ),
        "accepted_task_types": [
            "ops", "mysql", "redis", "kubernetes", "k8s",
            "logs", "prometheus", "monitoring", "debug-infra", "sre", "devops",
        ],
        "max_tool_rounds": 15,
        "timeout_seconds": 600,
    },
]


class SubAgentRegistry:
    """SubAgent 角色注册和生命周期管理。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def ensure_defaults(self) -> None:
        """确保默认 SubAgent 角色存在且配置为最新版本（幂等）。

        - 不存在：创建
        - 已存在但配置不同：更新 role_prompt 和 max_tool_rounds（保持其他用户配置不变）
        """
        for defn in DEFAULT_SUB_AGENTS:
            existing = await self._db.get(SubAgentDefinition, defn["name"])
            if existing is None:
                record = SubAgentDefinition(**defn)
                self._db.add(record)
                logger.info(f"创建默认 SubAgent 定义: {defn['name']}")
            else:
                # 更新 role_prompt
                if existing.role_prompt != defn.get("role_prompt"):
                    existing.role_prompt = defn.get("role_prompt", "")
                    logger.info(f"更新默认 SubAgent role_prompt: {defn['name']}")
                # 更新 max_tool_rounds
                default_rounds = defn.get("max_tool_rounds", 8)
                if existing.max_tool_rounds != default_rounds:
                    existing.max_tool_rounds = default_rounds
                    logger.info(f"更新默认 SubAgent max_tool_rounds: {defn['name']} -> {default_rounds}")
        await self._db.flush()

    async def list_agents(self) -> list[dict[str, Any]]:
        """列出所有 SubAgent 定义。"""
        result = await self._db.execute(
            select(SubAgentDefinition).order_by(SubAgentDefinition.created_at)
        )
        records = result.scalars().all()
        return [self._to_dict(r) for r in records]

    async def get_agent(self, name: str) -> dict[str, Any] | None:
        """获取单个 SubAgent 定义。"""
        record = await self._db.get(SubAgentDefinition, name)
        return self._to_dict(record) if record else None

    async def create_agent(self, data: dict[str, Any]) -> dict[str, Any]:
        """创建新的 SubAgent 定义。"""
        name = data.get("name", "")
        if not name:
            raise ValueError("SubAgent 名称不能为空")

        existing = await self._db.get(SubAgentDefinition, name)
        if existing:
            raise ValueError(f"SubAgent '{name}' 已存在")

        record = SubAgentDefinition(
            name=name,
            display_name=data.get("display_name", name),
            role_prompt=data.get("role_prompt", ""),
            accepted_task_types=data.get("accepted_task_types", []),
            model_name=data.get("model_name"),
            model_provider=data.get("model_provider"),
            model_api_key=data.get("model_api_key"),
            model_base_url=data.get("model_base_url"),
            max_tool_rounds=data.get("max_tool_rounds", 8),
            timeout_seconds=data.get("timeout_seconds", 300),
            enabled=data.get("enabled", True),
        )
        self._db.add(record)
        await self._db.flush()
        logger.info(f"创建 SubAgent: {name}")
        return self._to_dict(record)

    async def update_agent(self, name: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """更新 SubAgent 定义。"""
        record = await self._db.get(SubAgentDefinition, name)
        if record is None:
            return None

        updatable = [
            "display_name", "role_prompt", "accepted_task_types",
            "model_name", "model_provider", "model_api_key", "model_base_url",
            "max_tool_rounds", "timeout_seconds", "enabled",
        ]
        for key in updatable:
            if key in data:
                setattr(record, key, data[key])

        await self._db.flush()
        logger.info(f"更新 SubAgent: {name}")
        return self._to_dict(record)

    async def delete_agent(self, name: str) -> bool:
        """删除 SubAgent 定义。"""
        record = await self._db.get(SubAgentDefinition, name)
        if record is None:
            return False
        await self._db.delete(record)
        await self._db.flush()
        logger.info(f"删除 SubAgent: {name}")
        return True

    async def find_agent_for_task(
        self, task_type: str
    ) -> SubAgentDefinition | None:
        """根据任务类型找到合适的 SubAgent。

        遍历所有已启用的 SubAgent，检查其 accepted_task_types 是否包含
        给定的 task_type。返回第一个匹配的。
        """
        result = await self._db.execute(
            select(SubAgentDefinition).where(SubAgentDefinition.enabled == True)  # noqa: E712
        )
        records = result.scalars().all()

        for record in records:
            if task_type in (record.accepted_task_types or []):
                return record
        return None

    async def get_enabled_agents(self) -> list[SubAgentDefinition]:
        """获取所有已启用的 SubAgent 定义。"""
        result = await self._db.execute(
            select(SubAgentDefinition).where(SubAgentDefinition.enabled == True)  # noqa: E712
        )
        return list(result.scalars().all())

    async def build_roster_prompt(self) -> str:
        """动态生成 SubAgent roster，注入到 system prompt 中。

        仅包含已启用的 SubAgent。每轮对话重新查询 DB，
        新增/修改/禁用的 SubAgent 即时生效。

        Returns:
            格式化的 roster prompt 字符串。无可用 SubAgent 时返回空串。
        """
        agents = await self.get_enabled_agents()
        if not agents:
            return ""

        parts: list[str] = [
            "## Available SubAgents\n",
            "You can delegate tasks to the following specialized SubAgents "
            "by calling `dispatch_sub_agent`.\n"
            "Only dispatch when the task genuinely benefits from a specialist.\n"
            "**IMPORTANT:** Always use `blocking=false` (the default) so the SubAgent "
            "runs asynchronously and reports results back. Never use `blocking=true` "
            "unless the user explicitly asks to wait.\n",
        ]

        for agent in agents:
            display = agent.display_name or agent.name
            task_types = ", ".join(agent.accepted_task_types or [])
            # 取 role_prompt 首行做摘要，避免 context 膨胀
            summary = (agent.role_prompt or "").split("\n")[0].strip()
            parts.append(
                f"### {display} (`{agent.name}`)\n"
                f"- **Task types:** {task_types}\n"
                f"- **Specialty:** {summary}\n"
                f"- **To dispatch:** `dispatch_sub_agent(agent_name=\"{agent.name}\", ...)`\n"
            )

        return "\n".join(parts)

    @staticmethod
    def _to_dict(record: SubAgentDefinition) -> dict[str, Any]:
        return {
            "name": record.name,
            "display_name": record.display_name,
            "role_prompt": record.role_prompt,
            "accepted_task_types": record.accepted_task_types or [],
            "model_name": record.model_name,
            "model_provider": record.model_provider,
            "model_base_url": record.model_base_url,
            "has_custom_model": bool(record.model_name),
            "max_tool_rounds": record.max_tool_rounds,
            "timeout_seconds": record.timeout_seconds,
            "enabled": record.enabled,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        }
