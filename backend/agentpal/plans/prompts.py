"""Plan Mode prompt 模板 — 各阶段的 system prompt 追加片段。"""

from __future__ import annotations

from agentpal.plans.store import Plan, PlanStep

# ── 计划生成 ──────────────────────────────────────────────

PLAN_GENERATION_PROMPT = """\

## Plan Mode — 完整计划辅导

你正在进入「先规划、后执行」模式。请为用户请求生成可落地的执行计划。

### 目标
- 把模糊需求拆成**可执行、可验证、可交付**的步骤
- 降低返工风险，先对齐方案再开始执行

### 适用任务
以下任务优先使用 Plan Mode：
- 多文件改动 / 架构调整 / 重构
- 需求不清晰，需要先探索
- 存在多种实现路径，需要权衡
- 涉及高风险操作（数据、生产、权限、安全）

### 规划流程（先想清楚再输出）
1. 明确目标与边界（做什么、不做什么）
2. 识别约束（技术栈、现有模式、安全限制）
3. 列出关键风险与验证策略
4. 拆分 2–6 个步骤（每步可由一个 SubAgent 独立完成）
5. 为每步给出建议工具与完成判定

### 高质量计划标准
- 步骤原子化：每步是单一职责，避免“查+改+测”混在一步
- 依赖清晰：后续步骤能够直接消费前序结果
- 可验证：每步都有可观察输出（代码变更、测试结果、产物）
- 风险可控：高风险步骤要写明回滚或降级策略
- 贴合代码库：优先沿用现有实现模式，不引入不必要抽象

### 输出格式（严格）
请只输出一个 ```json 代码块，字段必须包含 goal/summary/steps：

```json
{
  "goal": "一句话描述最终目标",
  "summary": "计划整体概述（1-2 句）",
  "steps": [
    {
      "index": 0,
      "title": "步骤标题",
      "description": "这一步具体要完成什么（含边界/验收点）",
      "strategy": "执行策略（方法、关键决策、风险控制）",
      "tools": ["建议使用的工具名"]
    }
  ]
}
```

### 约束
1. steps 长度必须在 2–6 之间
2. index 从 0 开始连续递增
3. tools 仅填写当前启用工具名；不确定时可留空数组
4. 可以先调用工具收集信息，再输出最终 JSON
5. 最终回复必须是可解析 JSON（放在 ```json 代码块中）
"""


# ── 确认阶段 ──────────────────────────────────────────────


def build_confirm_context(plan: Plan) -> str:
    """构建确认阶段的上下文，展示计划全文供用户审批。"""
    steps_text = ""
    for s in plan.steps:
        tools = ", ".join(s.tools) if s.tools else "无"
        steps_text += (
            f"\n### 步骤 {s.index + 1}: {s.title}\n"
            f"- 描述: {s.description}\n"
            f"- 策略: {s.strategy}\n"
            f"- 工具: {tools}\n"
        )

    return f"""\

## 当前计划 — 等待用户确认

**目标**: {plan.goal}
**概述**: {plan.summary}
**步骤数**: {len(plan.steps)}

{steps_text}

---

用户正在审阅计划。你的职责是“辅导确认”，不是直接开始编码。

### 用户可能的反馈类型
- **批准执行**（如：开始、同意、执行吧）
  - 明确告知：将进入执行阶段，并从第 1 步开始
- **要求修改**（如：删减步骤、调整顺序、补充风险）
  - 根据反馈重写完整计划
- **提问澄清**（如：为什么这样拆、某步做什么）
  - 用当前计划上下文解释，避免脱离计划
- **取消计划**
  - 确认取消并退出 Plan Mode

### 回复风格要求
- 简洁、结构化、可执行
- 不重复整段计划，优先回答用户关心点
- 若用户问题涉及风险，明确指出影响与替代方案
"""


# ── 执行阶段 ──────────────────────────────────────────────


def build_execution_context(plan: Plan) -> str:
    """构建执行阶段的上下文，注入当前步骤 + 前序结果。"""
    lines = [
        f"\n## 计划执行中",
        f"\n**目标**: {plan.goal}",
        f"**进度**: 步骤 {plan.current_step + 1}/{len(plan.steps)}",
        "",
    ]

    for s in plan.steps:
        status_icon = {
            "completed": "✅",
            "running": "⏳",
            "failed": "❌",
            "skipped": "⏭",
            "pending": "⬜",
        }.get(s.status, "⬜")

        lines.append(f"{status_icon} 步骤 {s.index + 1}: {s.title} [{s.status}]")
        if s.result:
            # 截断长结果
            result_preview = s.result[:200] + ("..." if len(s.result) > 200 else "")
            lines.append(f"   结果: {result_preview}")

    lines.append("\n---")
    lines.append("计划正在执行中。SubAgent 完成每一步后会自动推进下一步。")
    lines.append("如果用户中途提问：先回答用户问题，再继续保持计划上下文。")
    lines.append("不要重复输出整个计划，优先汇报当前步骤状态、阻塞点和下一步动作。")

    return "\n".join(lines)


# ── 步骤 Prompt（给 SubAgent 的任务描述）─────────────────


def build_step_prompt(plan: Plan, step: PlanStep) -> str:
    """构造 SubAgent 任务 prompt。"""
    # 收集前序步骤结果
    prev_results = ""
    for s in plan.steps:
        if s.index >= step.index:
            break
        if s.status == "completed" and s.result:
            result_text = s.result[:500] + ("..." if len(s.result) > 500 else "")
            prev_results += f"\n### 步骤 {s.index + 1} ({s.title}) 结果:\n{result_text}\n"

    prompt_parts = [
        f"# 任务: {step.title}",
        f"\n## 总体目标\n{plan.goal}",
        f"\n## 当前步骤（{step.index + 1}/{len(plan.steps)}）",
        f"\n### 描述\n{step.description}",
    ]

    if step.strategy:
        prompt_parts.append(f"\n### 执行策略\n{step.strategy}")

    if prev_results:
        prompt_parts.append(f"\n## 前序步骤结果\n{prev_results}")

    prompt_parts.append(
        "\n## 执行要求\n"
        "- 聚焦本步骤，不要扩展到未分配步骤\n"
        "- 先收集证据再下结论，必要时调用搜索/读取工具\n"
        "- 给出可验证结果（命令输出、文件路径、关键变更点）\n"
        "- 涉及风险操作时先说明风险与回滚思路\n"
        "- 如果需要生成文件，使用 produce_artifact 工具保存"
    )

    prompt_parts.append(
        "\n## 返回格式\n"
        "请返回结构化结果，建议包含：\n"
        "1) 完成情况\n"
        "2) 关键发现或变更\n"
        "3) 验证结果\n"
        "4) 风险与后续建议（如有）"
    )

    return "\n".join(prompt_parts)


# ── 修改计划 Prompt ───────────────────────────────────────


def build_revise_prompt(plan: Plan, user_feedback: str) -> str:
    """构建修改计划的 prompt。"""
    return f"""\
## Plan Mode — 修改计划

用户对以下计划提出了修改意见，请基于反馈重写完整计划。

### 当前计划
- 目标: {plan.goal}
- 概述: {plan.summary}
- 步骤数: {len(plan.steps)}

### 用户反馈
{user_feedback}

### 修改要求
1. 保留用户已认可的部分，优先修改被明确指出的问题
2. 保持步骤数在 2–6 步
3. 每一步仍需包含 title/description/strategy/tools
4. 如果反馈涉及风险、范围或顺序，必须在对应步骤体现
5. 最终输出只能是 ```json 代码块（可解析）

请输出修改后的完整计划（使用与之前相同的 JSON 格式）。
"""
