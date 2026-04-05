"""IntentClassifier — 轻量关键词意图识别（Plan Mode 触发/退出/确认）。

不依赖 LLM，纯规则匹配。``unknown`` 意图不做状态转换，
交给 LLM 在当前计划上下文中回答用户。

触发匹配使用正则表达式，容忍中间插入修饰词（"一个"/"一下"/"个"等）。
"""

from __future__ import annotations

import re


class IntentClassifier:
    """Plan Mode 意图分类器。"""

    # ── 触发正则 ─────────────────────────────────────────
    # 允许核心动词和名词之间有 0~6 个汉字（修饰词，如 "一个"、"一个详细的"）
    _PLAN_TRIGGER_PATTERNS: list[re.Pattern[str]] = [
        re.compile(r"制定.{0,6}计划"),
        re.compile(r"做.{0,6}计划"),
        re.compile(r"制定.{0,6}方案"),
        re.compile(r"做.{0,6}方案"),
        re.compile(r"帮我.{0,6}规划"),
        re.compile(r"先.{0,4}规划"),
        re.compile(r"请.{0,4}规划"),
        re.compile(r"帮我.{0,6}计划"),
        re.compile(r"规划.{0,4}并"),      # "规划xxx并实现"
        re.compile(r"拆解.{0,6}任务"),
        re.compile(r"任务.{0,4}拆解"),
        re.compile(r"实施.{0,4}计划"),
        re.compile(r"执行.{0,4}方案"),
        re.compile(r"先.{0,8}(别|不要).{0,8}写代码.{0,8}(计划|方案|思路)"),
        re.compile(r"/plan\b"),
        re.compile(r"plan\s+this"),
        re.compile(r"plan\s+it"),
        re.compile(r"make\s+a\s+plan"),
        re.compile(r"create\s+a\s+plan"),
        re.compile(r"implementation\s+plan"),
        re.compile(r"break\s+.*\s+down"),
        re.compile(r"(make|create|build)\s+.*roadmap"),
        re.compile(r"roadmap\s+for"),
    ]

    _EXIT_PLAN_PATTERNS: list[re.Pattern[str]] = [
        re.compile(r"取消.{0,2}计划"),
        re.compile(r"退出.{0,2}计划"),
        re.compile(r"不要.{0,2}计划"),
        re.compile(r"放弃.{0,2}计划"),
        re.compile(r"停止.{0,2}计划"),
        re.compile(r"/exit[-_]?plan\b"),
        re.compile(r"cancel\s+plan"),
        re.compile(r"exit\s+plan"),
        re.compile(r"abort\s+plan"),
    ]

    APPROVE_TRIGGERS: list[str] = [
        "批准",
        "通过",
        "执行吧",
        "开始",
        "可以",
        "没问题",
        "开始执行",
        "就这样",
        "确认",
        "同意",
        "好的",
        "行，开始",
        "开干",
        "approve",
        "go",
        "lgtm",
        "proceed",
        "start",
        "yes",
        "ok",
    ]

    MODIFY_TRIGGERS: list[str] = [
        "修改",
        "改一下",
        "调整",
        "不对",
        "重新",
        "换一下",
        "补充",
        "细化",
        "精简",
        "modify",
        "revise",
        "change",
        "update",
        "refine",
    ]

    @staticmethod
    def is_plan_trigger(text: str) -> bool:
        """检测是否触发计划模式。

        使用正则匹配，容忍核心词之间的修饰词，
        例如 "制定一个计划"、"帮我做个详细的方案" 均可匹配。
        """
        normalized = text.strip().lower()
        return any(p.search(normalized) for p in IntentClassifier._PLAN_TRIGGER_PATTERNS)

    @staticmethod
    def is_exit_plan(text: str) -> bool:
        """检测是否退出计划模式。"""
        normalized = text.strip().lower()
        return any(p.search(normalized) for p in IntentClassifier._EXIT_PLAN_PATTERNS)

    @staticmethod
    def classify_confirm(text: str) -> str:
        """分类用户在确认阶段的意图。

        Returns:
            ``"approve"`` / ``"modify"`` / ``"cancel"`` / ``"unknown"``
        """
        normalized = text.strip().lower()

        # 取消优先级最高
        if IntentClassifier.is_exit_plan(text):
            return "cancel"

        # 修改
        if any(t in normalized for t in IntentClassifier.MODIFY_TRIGGERS):
            return "modify"

        # 批准
        if any(t in normalized for t in IntentClassifier.APPROVE_TRIGGERS):
            return "approve"

        return "unknown"
