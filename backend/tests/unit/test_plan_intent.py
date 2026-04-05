"""Unit tests for IntentClassifier — Plan Mode intent recognition."""

from __future__ import annotations

import pytest

from agentpal.plans.intent import IntentClassifier


# ── is_plan_trigger ──────────────────────────────────────


class TestIsPlanTrigger:
    def test_chinese_triggers(self) -> None:
        assert IntentClassifier.is_plan_trigger("帮我做个计划") is True
        assert IntentClassifier.is_plan_trigger("先规划一下") is True
        assert IntentClassifier.is_plan_trigger("制定计划") is True
        assert IntentClassifier.is_plan_trigger("请帮我制定计划吧") is True
        assert IntentClassifier.is_plan_trigger("帮我拆解这个任务") is True
        assert IntentClassifier.is_plan_trigger("先不要写代码，先给我一个实施方案") is True
        # 中间有修饰词也能匹配
        assert IntentClassifier.is_plan_trigger("帮我制定一个计划：写一个电扇demo") is True
        assert IntentClassifier.is_plan_trigger("做一个详细的计划") is True
        assert IntentClassifier.is_plan_trigger("帮我制定一个详细的方案") is True

    def test_english_triggers(self) -> None:
        assert IntentClassifier.is_plan_trigger("/plan") is True
        assert IntentClassifier.is_plan_trigger("plan this task") is True
        assert IntentClassifier.is_plan_trigger("plan it") is True
        assert IntentClassifier.is_plan_trigger("make a plan for this") is True
        assert IntentClassifier.is_plan_trigger("create a plan") is True
        assert IntentClassifier.is_plan_trigger("need an implementation plan") is True
        assert IntentClassifier.is_plan_trigger("break this task down") is True
        assert IntentClassifier.is_plan_trigger("create a roadmap for migration") is True

    def test_not_plan_trigger(self) -> None:
        assert IntentClassifier.is_plan_trigger("你好") is False
        assert IntentClassifier.is_plan_trigger("帮我写代码") is False
        assert IntentClassifier.is_plan_trigger("这个计划怎么样") is False  # 包含"计划"但不是触发词
        assert IntentClassifier.is_plan_trigger("请更新项目 roadmap 文档") is False
        assert IntentClassifier.is_plan_trigger("") is False

    def test_case_insensitive(self) -> None:
        assert IntentClassifier.is_plan_trigger("/PLAN") is True
        assert IntentClassifier.is_plan_trigger("Plan This") is True


# ── is_exit_plan ─────────────────────────────────────────


class TestIsExitPlan:
    def test_chinese_exits(self) -> None:
        assert IntentClassifier.is_exit_plan("取消计划") is True
        assert IntentClassifier.is_exit_plan("退出计划") is True
        assert IntentClassifier.is_exit_plan("不要计划了") is True

    def test_english_exits(self) -> None:
        assert IntentClassifier.is_exit_plan("/exit-plan") is True
        assert IntentClassifier.is_exit_plan("cancel plan") is True
        assert IntentClassifier.is_exit_plan("abort plan") is True

    def test_not_exit(self) -> None:
        assert IntentClassifier.is_exit_plan("继续") is False
        assert IntentClassifier.is_exit_plan("开始执行") is False


# ── classify_confirm ─────────────────────────────────────


class TestClassifyConfirm:
    def test_approve(self) -> None:
        assert IntentClassifier.classify_confirm("批准") == "approve"
        assert IntentClassifier.classify_confirm("通过") == "approve"
        assert IntentClassifier.classify_confirm("执行吧") == "approve"
        assert IntentClassifier.classify_confirm("可以") == "approve"
        assert IntentClassifier.classify_confirm("没问题") == "approve"
        assert IntentClassifier.classify_confirm("开始") == "approve"
        assert IntentClassifier.classify_confirm("go") == "approve"
        assert IntentClassifier.classify_confirm("lgtm") == "approve"
        assert IntentClassifier.classify_confirm("好的") == "approve"
        assert IntentClassifier.classify_confirm("ok") == "approve"

    def test_modify(self) -> None:
        assert IntentClassifier.classify_confirm("修改一下第三步") == "modify"
        assert IntentClassifier.classify_confirm("调整步骤顺序") == "modify"
        assert IntentClassifier.classify_confirm("不对，需要改一下") == "modify"
        assert IntentClassifier.classify_confirm("补充一个风险评估步骤") == "modify"
        assert IntentClassifier.classify_confirm("细化第二步") == "modify"
        assert IntentClassifier.classify_confirm("revise the plan") == "modify"

    def test_cancel(self) -> None:
        assert IntentClassifier.classify_confirm("取消计划") == "cancel"
        assert IntentClassifier.classify_confirm("退出计划吧") == "cancel"
        assert IntentClassifier.classify_confirm("/exit-plan") == "cancel"

    def test_cancel_priority_over_modify(self) -> None:
        """Cancel has higher priority than modify."""
        # "取消计划" contains exit trigger, should be cancel even with modify words
        assert IntentClassifier.classify_confirm("取消计划，不要修改了") == "cancel"

    def test_unknown(self) -> None:
        assert IntentClassifier.classify_confirm("第三步具体怎么做？") == "unknown"
        assert IntentClassifier.classify_confirm("这个计划要多久？") == "unknown"
        assert IntentClassifier.classify_confirm("解释一下策略") == "unknown"
