"""MemoryWriter — 对话压缩与长期记忆写入。

工作原理：
  1. 定期摘要（基于消息条数）：
     每当会话对话轮次达到 COMPACTION_THRESHOLD 的倍数时，
     异步调用 LLM 提炼近期对话中的事实和摘要，写入：
       - MEMORY.md：值得长期记住的事实
       - memory/YYYY-MM-DD.md：今日活动摘要

  2. Token 压缩（基于上下文窗口）：
     当 session.context_tokens ≥ 80% * context_window 时，
     后台自动压缩旧消息（软标记 compressed=true），
     插入一条摘要消息，重置 context_tokens。
     前端以灰色/半透明显示已压缩消息，摘要消息渲染为分隔线。
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid

from loguru import logger

from agentpal.memory.base import BaseMemory, MemoryMessage, MemoryRole

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

_COMPRESS_PROMPT = """\
你是一个对话摘要助手。请将以下对话压缩为一个简洁的摘要，保留关键信息。

对话内容：
{conversation}

要求：
- 以「【对话摘要】」开头
- 200-500 字之间
- 保留重要的上下文信息（用户需求、讨论结论、重要决定等）
- 保留工具调用的关键结果
- 不需要逐条复述，只需概括主题和结论
- 使用简洁的中文

请直接输出摘要文本，不要有额外的格式或标记。
"""

# 压缩时保留最近 N 条消息不被压缩
KEEP_RECENT = 6

# 压缩触发阈值：context_tokens >= context_window * COMPRESS_THRESHOLD_RATIO
COMPRESS_THRESHOLD_RATIO = 0.8

# 压缩后重置 context_tokens 到此值（摘要自身的估计 token 数）
COMPRESS_RESET_TOKENS = 3000


class MemoryWriter:
    """长期记忆写入器。

    Args:
        compaction_threshold: 每累计多少轮对话触发一次记忆提炼，默认 30
    """

    # 类级别：防止同一 session 并发压缩
    _active_compressions: set[str] = set()

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
                # 后台任务使用独立 DB session，不复用请求级 memory
                asyncio.create_task(
                    self._flush_background(session_id, ws_manager, model_config)
                )
        except Exception as e:
            logger.warning(f"MemoryWriter.maybe_flush 检查失败: {e}")

    async def maybe_compress(
        self,
        session_id: str,
        memory: BaseMemory,
        ws_manager: "WorkspaceManager",  # type: ignore[name-defined]  # noqa: F821
        model_config: dict,
        context_tokens: int,
        context_window: int,
        db: "AsyncSession | None" = None,  # type: ignore[name-defined]  # noqa: F821
    ) -> None:
        """基于 token 阈值检查是否需要压缩上下文。

        当 context_tokens >= 80% * context_window 时，后台异步启动压缩。
        旧消息不删除，而是标记 compressed=true，插入摘要消息。

        Args:
            session_id:     会话 ID
            memory:         记忆后端实例
            ws_manager:     WorkspaceManager 实例
            model_config:   模型配置 dict
            context_tokens: 当前累计的 context token 数
            context_window: 模型上下文窗口大小（0 = 禁用）
            db:             请求级 AsyncSession（仅用于读 session 信息，压缩内部用独立 session）
        """
        # 功能关闭
        if context_window <= 0:
            return

        # 未达到阈值
        threshold = int(context_window * COMPRESS_THRESHOLD_RATIO)
        if context_tokens < threshold:
            return

        # 防重入
        if session_id in self._active_compressions:
            logger.debug(
                f"MemoryWriter: session={session_id} 已在压缩中，跳过"
            )
            return

        logger.info(
            f"MemoryWriter: session={session_id} context_tokens={context_tokens} "
            f">= {threshold} (80% of {context_window}), triggering token-based compression"
        )

        self._active_compressions.add(session_id)
        asyncio.create_task(
            self._compress(session_id, memory, ws_manager, model_config)
        )

    async def _flush_background(
        self,
        session_id: str,
        ws_manager: "WorkspaceManager",  # type: ignore[name-defined]  # noqa: F821
        model_config: dict,
    ) -> None:
        """后台记忆提炼：使用独立 DB session，避免复用已关闭的请求级 session。"""
        try:
            from agentpal.database import AsyncSessionLocal
            from agentpal.memory.factory import MemoryFactory

            async with AsyncSessionLocal() as bg_db:
                bg_memory = MemoryFactory.create("sqlite", db=bg_db)
                await self._flush(session_id, bg_memory, ws_manager, model_config)
                await bg_db.commit()
        except Exception as e:
            logger.warning(f"MemoryWriter._flush_background 执行失败 (session={session_id}): {e}")

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

    async def _compress(
        self,
        session_id: str,
        memory: BaseMemory,
        ws_manager: "WorkspaceManager",  # type: ignore[name-defined]  # noqa: F821
        model_config: dict,
    ) -> None:
        """执行 token 压缩：LLM 生成摘要 → 标记旧消息 → 插入摘要消息 → 重置 tokens。

        使用独立的 AsyncSessionLocal() 数据库 session，避免复用请求级 session。
        所有 DB 操作都通过 bg_memory（基于独立 session）执行，不触碰请求级 memory。
        """
        try:
            from agentpal.database import AsyncSessionLocal
            from agentpal.memory.factory import MemoryFactory

            async with AsyncSessionLocal() as bg_db:
                # 创建独立的 memory 实例，使用 bg_db，避免复用请求级 session
                bg_memory = MemoryFactory.create("sqlite", db=bg_db)

                # 1. 加载全部消息
                all_msgs = await bg_memory.get_recent(session_id, limit=10_000)
                if len(all_msgs) <= KEEP_RECENT:
                    logger.info(
                        f"MemoryWriter._compress: session={session_id} "
                        f"消息数 {len(all_msgs)} <= {KEEP_RECENT}，无需压缩"
                    )
                    return

                # 2. 分为旧消息和最近保留的消息
                old_msgs = all_msgs[:-KEEP_RECENT]
                # 只压缩尚未被压缩的旧消息
                uncompressed_old = [
                    m for m in old_msgs
                    if not (m.metadata or {}).get("compressed")
                ]
                if not uncompressed_old:
                    logger.info(
                        f"MemoryWriter._compress: session={session_id} "
                        f"所有旧消息已被压缩，跳过"
                    )
                    return

                # 3. 构建对话文本用于 LLM 摘要
                lines = []
                for m in uncompressed_old:
                    role_label = {"user": "用户", "assistant": "助手"}.get(
                        str(m.role), str(m.role)
                    )
                    content = m.content[:500] + ("…" if len(m.content) > 500 else "")
                    lines.append(f"{role_label}: {content}")
                conversation = "\n".join(lines)

                # 4. 调用 LLM 生成摘要
                from agentpal.agents.personal_assistant import _build_model, _extract_text

                model = _build_model(model_config)
                prompt = _COMPRESS_PROMPT.format(conversation=conversation)
                response = await model([{"role": "user", "content": prompt}])
                summary_text = _extract_text(response).strip()

                if not summary_text:
                    summary_text = "【对话摘要】（摘要生成失败，已压缩旧消息）"

                # 5. 调用 _flush 提取事实 → MEMORY.md + 日志（利用旧消息做事实提炼）
                try:
                    await self._flush(session_id, bg_memory, ws_manager, model_config)
                except Exception as flush_err:
                    logger.warning(
                        f"MemoryWriter._compress: _flush 失败（不阻塞压缩）: {flush_err}"
                    )

                # 6. 软标记旧消息为已压缩
                old_msg_ids = [m.id for m in uncompressed_old if m.id]
                marked = await bg_memory.mark_compressed(session_id, old_msg_ids)
                logger.info(
                    f"MemoryWriter._compress: 标记 {marked} 条旧消息为 compressed "
                    f"(session={session_id})"
                )

                # 7. 插入摘要消息
                compressed_count = len(uncompressed_old)
                summary_msg = MemoryMessage(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    role=MemoryRole.ASSISTANT,
                    content=summary_text,
                    metadata={
                        "type": "context_summary",
                        "compressed_count": compressed_count,
                    },
                )
                await bg_memory.add(summary_msg)
                logger.info(
                    f"MemoryWriter._compress: 插入摘要消息 "
                    f"(compressed_count={compressed_count}, session={session_id})"
                )

                # 8. 重置 session.context_tokens
                from sqlalchemy import text as sa_text

                await bg_db.execute(
                    sa_text(
                        "UPDATE sessions SET context_tokens = :tokens WHERE id = :sid"
                    ),
                    {"tokens": COMPRESS_RESET_TOKENS, "sid": session_id},
                )
                await bg_db.commit()
                logger.info(
                    f"MemoryWriter._compress: 重置 context_tokens={COMPRESS_RESET_TOKENS} "
                    f"(session={session_id})"
                )

        except Exception as e:
            logger.warning(
                f"MemoryWriter._compress 执行失败 (session={session_id}): {e}"
            )
        finally:
            self._active_compressions.discard(session_id)
