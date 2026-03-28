"""Plan Mode prompt 模板 — 各阶段的 system prompt 追加片段。"""

from __future__ import annotations

from typing import Any

from agentpal.plans.store import Plan, PlanStep

# ── 计划生成 ──────────────────────────────────────────────

PLAN_GENERATION_PROMPT = """\

## Plan Mode — 任务拆解

用户请求你为以下任务制定一个执行计划。请将任务拆解为 **2–6 个**可执行步骤。

**输出格式**（严格按照以下 JSON 格式输出，放在 ```json 代码块中）：

```json
{
  "goal": "一句话描述最终目标",
  "summary": "计划整体概述（1-2 句）",
  "steps": [
    {
      "index": 0,
      "title": "步骤标题",
      "description": "详细描述这一步要做什么",
      "strategy": "执行策略说明（用什么方法、工具）",
      "tools": ["可能用到的工具名"]
    }
  ]
}
```

**规则**：
1. 每个步骤应该是可由一个 SubAgent 独立执行的原子任务
2. 步骤之间应有清晰的依赖关系（后续步骤可以引用前序步骤的结果）
3. 工具列表从当前启用的工具中选择
4. 如果需要，你可以先调用工具收集信息，然后再输出计划
5. 最终必须输出包含 JSON 代码块的计划
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

用户正在审阅此计划。请根据用户的回复进行操作：
- 如果用户表示同意/批准 → 告知用户计划开始执行
- 如果用户提出修改建议 → 根据建议调整计划
- 如果用户问问题 → 在计划上下文中回答
- 如果用户取消 → 确认取消
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
    lines.append("如果用户发消息，在计划上下文中正常回复。")

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
        "\n## 要求\n"
        "- 完成上述任务并返回执行结果\n"
        "- 如果需要生成文件，使用 produce_artifact 工具\n"
        "- 结果会传递给后续步骤使用"
    )

    return "\n".join(prompt_parts)


# ── 修改计划 Prompt ───────────────────────────────────────


def build_revise_prompt(plan: Plan, user_feedback: str) -> str:
    """构建修改计划的 prompt。"""
    return f"""\
## Plan Mode — 修改计划

用户对以下计划提出了修改意见，请根据反馈调整计划。

### 当前计划
- 目标: {plan.goal}
- 步骤数: {len(plan.steps)}

### 用户反馈
{user_feedback}

请输出修改后的完整计划（使用与之前相同的 JSON 格式，放在 ```json 代码块中）。
"""
