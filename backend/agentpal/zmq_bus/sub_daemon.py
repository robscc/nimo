"""SubAgentDaemon — SubAgent 守护进程。

每个 SubAgent 任务对应一个 SubAgentDaemon 实例：
- identity 格式: "sub:{agent_name}:{task_id}"
- 收到 DISPATCH_TASK 后创建独立 DB session，加载 SubAgentTask，执行任务
- 通过 PUB socket 在 topic "task:{task_id}" 上发布任务事件
- 执行完毕后向 PA daemon 发送 AGENT_RESPONSE
- 支持 AGENT_REQUEST（Agent 间通信）和 AGENT_SHUTDOWN（优雅关闭）
"""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from agentpal.zmq_bus.daemon import AgentDaemon
from agentpal.zmq_bus.protocol import Envelope, MessageType


class SubAgentDaemon(AgentDaemon):
    """SubAgent 守护进程。

    每个实例绑定一个 agent_name + task_id，执行单次任务后可被 manager 回收。

    Args:
        agent_name:        SubAgent 角色名称（如 "coder"、"researcher"）
        task_id:           任务 ID
        model_config:      LLM 模型配置（可由 SubAgentDefinition 覆盖）
        role_prompt:       角色系统提示词
        max_tool_rounds:   最大工具调用轮次
        parent_session_id: 父会话 ID（用于关联 PA daemon）
    """

    def __init__(
        self,
        agent_name: str,
        task_id: str,
        model_config: dict[str, Any] | None = None,
        role_prompt: str = "",
        max_tool_rounds: int = 8,
        parent_session_id: str = "",
    ) -> None:
        identity = f"sub:{agent_name}:{task_id}"
        super().__init__(identity=identity)

        self._agent_name = agent_name
        self._task_id = task_id
        self._model_config = model_config or {}
        self._role_prompt = role_prompt
        self._max_tool_rounds = max_tool_rounds
        self._parent_session_id = parent_session_id

    # ── 消息处理 ────────────────────────────────────────────

    async def handle_message(self, envelope: Envelope) -> None:
        """根据消息类型分发处理。

        支持的消息类型：
        - DISPATCH_TASK:   执行 SubAgent 任务
        - AGENT_REQUEST:   处理来自其他 Agent 的协作请求
        - AGENT_SHUTDOWN:  优雅关闭
        """
        handlers = {
            MessageType.DISPATCH_TASK: self._handle_dispatch_task,
            MessageType.AGENT_REQUEST: self._handle_agent_request,
            MessageType.AGENT_SHUTDOWN: self._handle_shutdown,
        }

        handler = handlers.get(envelope.msg_type)
        if handler is None:
            logger.warning(
                f"SubAgentDaemon [{self.identity}] 收到未知消息类型: "
                f"{envelope.msg_type}"
            )
            return

        await handler(envelope)

    # ── DISPATCH_TASK 处理 ──────────────────────────────────

    async def _handle_dispatch_task(self, envelope: Envelope) -> None:
        """处理任务派遣消息：创建独立 DB session，运行 SubAgent，发布事件。

        payload 字段：
        - task_id:            任务 ID（应与 self._task_id 一致）
        - task_prompt:        任务描述
        - parent_session_id:  父会话 ID（可选，覆盖构造函数值）

        兜底机制：无论正常完成、业务异常还是意外异常（BaseException），
        finally 块都会检查 _response_sent 标记，确保 AGENT_RESPONSE
        至少发送一次，避免 broker 侧状态卡在 RUNNING。
        """
        payload = envelope.payload
        task_id = payload.get("task_id", self._task_id)
        task_prompt = payload.get("task_prompt", "")
        parent_session_id = payload.get(
            "parent_session_id", self._parent_session_id
        )

        # 从 payload 更新配置（create_sub_daemon 通过 envelope 传递）
        if payload.get("model_config"):
            self._model_config = payload["model_config"]
        if payload.get("role_prompt"):
            self._role_prompt = payload["role_prompt"]
        if payload.get("max_tool_rounds"):
            self._max_tool_rounds = payload["max_tool_rounds"]
        if parent_session_id:
            self._parent_session_id = parent_session_id

        topic = f"task:{task_id}"
        response_sent = False  # 兜底标记：是否已成功发送 AGENT_RESPONSE

        logger.info(
            f"SubAgentDaemon [{self.identity}] 开始执行任务: "
            f"task_id={task_id}"
        )

        # 发布任务开始事件
        await self._publish_task_event(
            topic=topic,
            event_type="task.started",
            data={"task_id": task_id, "agent_name": self._agent_name},
            message="任务开始执行",
        )

        try:
            result = await self._run_task(
                task_id=task_id,
                task_prompt=task_prompt,
                parent_session_id=parent_session_id,
                topic=topic,
            )

            # 发布任务完成事件
            await self._publish_task_event(
                topic=topic,
                event_type="task.completed",
                data={
                    "task_id": task_id,
                    "agent_name": self._agent_name,
                    "result": result[:500] if result else None,
                },
                message="任务执行完成",
            )

            # 向 PA daemon 发送 AGENT_RESPONSE
            response_envelope = envelope.make_reply(
                msg_type=MessageType.AGENT_RESPONSE,
                payload={
                    "task_id": task_id,
                    "status": "done",
                    "result": result,
                    "agent_name": self._agent_name,
                },
                source=self.identity,
            )
            await self.send_to_router(response_envelope)
            response_sent = True

            logger.info(
                f"SubAgentDaemon [{self.identity}] 任务完成: task_id={task_id}"
            )

        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            logger.error(
                f"SubAgentDaemon [{self.identity}] 任务失败: "
                f"task_id={task_id} error={exc}"
            )

            # 发布任务失败事件
            await self._publish_task_event(
                topic=topic,
                event_type="task.failed",
                data={
                    "task_id": task_id,
                    "agent_name": self._agent_name,
                    "error": error_msg[:500],
                },
                message="任务执行失败",
            )

            # 向 PA daemon 发送失败响应
            error_envelope = envelope.make_reply(
                msg_type=MessageType.AGENT_RESPONSE,
                payload={
                    "task_id": task_id,
                    "status": "failed",
                    "error": error_msg,
                    "agent_name": self._agent_name,
                },
                source=self.identity,
            )
            await self.send_to_router(error_envelope)
            response_sent = True

        finally:
            # 兜底：如果上面的 try/except 都没能成功发送 AGENT_RESPONSE
            # （如 send_to_router 抛异常、BaseException 绕过 except 等），
            # 在此做最后一次 best-effort 发送，确保 broker 能收到响应并
            # 将状态从 RUNNING 转回 IDLE/FAILED。
            if not response_sent:
                logger.warning(
                    f"SubAgentDaemon [{self.identity}] AGENT_RESPONSE 未发送，"
                    f"执行 finally 兜底: task_id={task_id}"
                )
                try:
                    fallback_envelope = envelope.make_reply(
                        msg_type=MessageType.AGENT_RESPONSE,
                        payload={
                            "task_id": task_id,
                            "status": "failed",
                            "error": "SubAgent 异常退出，AGENT_RESPONSE 由 finally 兜底发送",
                            "agent_name": self._agent_name,
                        },
                        source=self.identity,
                    )
                    await self.send_to_router(fallback_envelope)
                    logger.info(
                        f"SubAgentDaemon [{self.identity}] finally 兜底 "
                        f"AGENT_RESPONSE 已发送: task_id={task_id}"
                    )
                except Exception as fallback_exc:
                    # best-effort：连兜底都失败了，只能靠 broker 侧
                    # 的 RUNNING 超时机制（防线2）来兜底
                    logger.error(
                        f"SubAgentDaemon [{self.identity}] finally 兜底发送失败: "
                        f"task_id={task_id} error={fallback_exc}"
                    )

    async def _run_task(
        self,
        task_id: str,
        task_prompt: str,
        parent_session_id: str,
        topic: str,
    ) -> str:
        """在独立 DB session 中运行 SubAgent 任务。

        独立 session 确保不与请求级 session 冲突，
        执行完毕后自动 commit 并关闭连接。
        """
        from agentpal.agents.sub_agent import SubAgent
        from agentpal.database import AsyncSessionLocal
        from agentpal.memory.factory import MemoryFactory
        from agentpal.models.session import SubAgentTask

        sub_session_id = f"sub:{parent_session_id}:{task_id}"

        async with AsyncSessionLocal() as db:
            # 从数据库加载任务记录
            task = await db.get(SubAgentTask, task_id)
            if task is None:
                error = f"SubAgent 任务记录不存在: {task_id}"
                logger.error(error)
                raise ValueError(error)

            # 发布任务加载成功事件
            await self._publish_task_event(
                topic=topic,
                event_type="task.loaded",
                data={
                    "task_id": task_id,
                    "agent_name": self._agent_name,
                    "status": task.status if task.status else "unknown",
                },
                message="任务记录加载成功",
            )

            # 创建独立内存和 SubAgent 实例
            sub_memory = MemoryFactory.create("buffer")
            sub_agent = SubAgent(
                session_id=sub_session_id,
                memory=sub_memory,
                task=task,
                db=db,
                model_config=self._model_config,
                role_prompt=self._role_prompt,
                max_tool_rounds=self._max_tool_rounds,
                parent_session_id=parent_session_id,
            )

            # 执行任务
            result = await sub_agent.run(task_prompt)

            # 提交数据库变更（状态、执行日志等）
            await db.commit()

            # 发布执行日志事件（仅摘要，完整日志已持久化到 DB）
            log_count = len(task.execution_log) if task.execution_log else 0
            await self._publish_task_event(
                topic=topic,
                event_type="task.log_summary",
                data={
                    "task_id": task_id,
                    "log_entries": log_count,
                    "final_status": task.status if task.status else "unknown",
                },
                message=f"执行日志共 {log_count} 条",
            )

            return result

    # ── AGENT_REQUEST 处理 ──────────────────────────────────

    async def _handle_agent_request(self, envelope: Envelope) -> None:
        """处理来自其他 Agent 的协作请求。

        payload 字段：
        - content:    请求内容
        - from_agent: 发送方名称

        当前实现：将请求记录到日志并回复确认。
        后续可扩展为在任务执行中注入外部消息。
        """
        payload = envelope.payload
        from_agent = payload.get("from_agent", envelope.source)
        content = payload.get("content", "")

        logger.info(
            f"SubAgentDaemon [{self.identity}] 收到协作请求: "
            f"from={from_agent} content={content[:100]}"
        )

        # 发布收到协作请求的事件
        topic = f"task:{self._task_id}"
        await self._publish_task_event(
            topic=topic,
            event_type="agent.request_received",
            data={
                "from_agent": from_agent,
                "content": content[:500],
                "task_id": self._task_id,
            },
            message=f"收到来自 {from_agent} 的协作请求",
        )

        # 回复确认
        reply = envelope.make_reply(
            msg_type=MessageType.AGENT_RESPONSE,
            payload={
                "task_id": self._task_id,
                "agent_name": self._agent_name,
                "status": "acknowledged",
                "message": f"SubAgent [{self._agent_name}] 已收到请求",
            },
            source=self.identity,
        )
        await self.send_to_router(reply)

    # ── AGENT_SHUTDOWN 处理 ─────────────────────────────────

    async def _handle_shutdown(self, envelope: Envelope) -> None:
        """处理优雅关闭请求。

        payload 字段：
        - reason: 关闭原因（可选）
        """
        reason = envelope.payload.get("reason", "管理器请求关闭")
        logger.info(
            f"SubAgentDaemon [{self.identity}] 收到关闭请求: reason={reason}"
        )

        # 发布关闭事件
        topic = f"task:{self._task_id}"
        await self._publish_task_event(
            topic=topic,
            event_type="agent.shutdown",
            data={
                "task_id": self._task_id,
                "agent_name": self._agent_name,
                "reason": reason,
            },
            message=f"SubAgent [{self._agent_name}] 正在关闭",
        )

        # 停止 daemon
        await self.stop()

    # ── 事件发布辅助 ────────────────────────────────────────

    async def _publish_task_event(
        self,
        topic: str,
        event_type: str,
        data: dict[str, Any],
        message: str = "",
    ) -> None:
        """通过 PUB socket 发布任务事件。

        Args:
            topic:      事件主题（如 "task:{task_id}"）
            event_type: 事件类型（如 "task.started"、"task.completed"）
            data:       事件数据
            message:    人类可读的事件描述
        """
        event_envelope = Envelope(
            msg_type=MessageType.TASK_EVENT,
            source=self.identity,
            target="*",  # 广播给所有订阅者
            session_id=self._parent_session_id or None,
            payload={
                "event_type": event_type,
                "data": data,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        await self.publish_event(topic, event_envelope)
