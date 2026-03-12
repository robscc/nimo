"""MemoryWriter — 对话压缩与长期记忆写入。

工作原理：
  每当会话对话轮次达到 COMPACTION_THRESHOLD 的倍数时，
  异步调用 LLM 提炼近期对话中的事实和摘要，写入：
    - MEMORY.md：值得长期记住的事实
    - memory/YYYY-MM-DD.md：今日活动摘要
"""

from __future__ import annotations

import asyncio
import json
import re

from loguru import logger

from agentpal.memory.base import BaseMemory

# 延迟导入避免循环依赖
_WORKSPACE_MANAGER_TYPE = None

_EXTRACT_PROMPT = """\
你是一个记忆整理助手。请分析以下对话片段，提取有价值的信息。

对话片段：
{conversation}

请提取：
1. **事实性记忆**：值得长期记住的事实，例如用户的个人信息、重要偏好、明确约定、完成的重要任务等。
   - 只记录明确提及的、具体的、有价值的信息
   - 不记录普通对话内容或临时性信息

2. **今日摘要**：用一两句话概括本次对话做了什么或讨论了什么。

请以 JSON 格式返回（不要有任何额外文字）：
{{
  "facts": ["事实1", "事实2"],
  "summary": "今日摘要"
}}

如果没有值得记录的事实，facts 返回空数组 []。
如果对话内容不值得记录摘要，summary 返回空字符串 ""。
"""


class MemoryWriter:
    """长期记忆写入器。

    Args:
        compaction_threshold: 每累计多少轮对话触发一次记忆提炼，默认 30
    """

    def __init__(self, compaction_threshold: int = 30) -> None:
        self.compaction_threshold = compaction_threshold

    async def maybe_flush(
        self,
        session_id: str,
        memory: BaseMemory,
        ws_manager: "WorkspaceManager",  # type: ignore[name-defined]  # noqa: F821
        model_config: dict,
    ) -> None:
        """检查是否需要触发记忆压缩，满足条件则在后台异步执行。

        在 PersonalAssistant.reply_stream() 的 done 事件后调用。
        不阻塞对话响应。
        """
        try:
            count = await memory.count(session_id)
            # 每达到 threshold 的整数倍时触发（且不为 0）
            if count > 0 and count % self.compaction_threshold == 0:
                logger.info(
                    f"MemoryWriter: session={session_id} count={count} "
                    f"→ 触发记忆压缩 (threshold={self.compaction_threshold})"
                )
                asyncio.create_task(
                    self._flush(session_id, memory, ws_manager, model_config)
                )
        except Exception as e:
            logger.warning(f"MemoryWriter.maybe_flush 检查失败: {e}")

    async def _flush(
        self,
        session_id: str,
        memory: BaseMemory,
        ws_manager: "WorkspaceManager",  # type: ignore[name-defined]  # noqa: F821
        model_config: dict,
    ) -> None:
        """执行记忆压缩：提炼事实 + 写入 MEMORY.md 和日志。"""
        try:
            # 取最近 N 条对话
            msgs = await memory.get_recent(
                session_id, limit=self.compaction_threshold
            )
            if not msgs:
                return

            # 构建对话文本（每条消息截断到 300 字符，避免 token 过多）
            lines = []
            for m in msgs:
                role_label = {"user": "用户", "assistant": "助手"}.get(
                    str(m.role), str(m.role)
                )
                content = m.content[:300] + ("…" if len(m.content) > 300 else "")
                lines.append(f"{role_label}: {content}")
            conversation = "\n".join(lines)

            # 调用 LLM 提炼
            from agentpal.agents.personal_assistant import _build_model, _extract_text

            model = _build_model(model_config)
            prompt = _EXTRACT_PROMPT.format(conversation=conversation)
            response = await model([{"role": "user", "content": prompt}])
            raw = _extract_text(response).strip()

            # 解析 JSON（兼容 LLM 在代码块中输出的情况）
            json_str = raw
            code_block = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", raw)
            if code_block:
                json_str = code_block.group(1)

            data = json.loads(json_str)
            facts: list[str] = data.get("facts", [])
            summary: str = data.get("summary", "")

            # 写入 MEMORY.md
            if facts:
                facts_md = "\n".join(f"- {f}" for f in facts)
                await ws_manager.append_memory(facts_md)
                logger.info(
                    f"MemoryWriter: 写入 {len(facts)} 条事实到 MEMORY.md "
                    f"(session={session_id})"
                )

            # 写入每日日志
            if summary:
                await ws_manager.append_daily_log(summary)
                logger.info(
                    f"MemoryWriter: 写入今日摘要到日志 (session={session_id})"
                )

        except json.JSONDecodeError as e:
            logger.warning(f"MemoryWriter: LLM 返回格式非 JSON，跳过写入: {e}")
        except Exception as e:
            logger.warning(f"MemoryWriter._flush 执行失败 (session={session_id}): {e}")
