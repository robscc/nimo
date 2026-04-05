"""PersonalAssistantDaemon — 每 Session 一个的主助手守护进程。

收到 CHAT_REQUEST 后调用 PersonalAssistant.reply_stream()，
将 SSE 事件通过 PUB socket 发布到 "session:{id}" topic。

生命周期：
- 由 AgentDaemonManager.ensure_pa_daemon() 按需创建
- 空闲 30 分钟后由 manager 自动回收
- 再次收到请求时重新创建，历史记忆从 DB warm-up
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

import asyncio

import zmq

from agentpal.zmq_bus.daemon import AgentDaemon
from agentpal.zmq_bus.protocol import Envelope, MessageType


class PersonalAssistantDaemon(AgentDaemon):
    """主助手守护进程。

    identity: ``pa:{session_id}``

    消息处理：
    - CHAT_REQUEST  → 调用 PA.reply_stream()，逐条发布 STREAM_EVENT
    - AGENT_NOTIFY  → SubAgent/Cron 完成通知，暂存供下次对话使用
    - AGENT_REQUEST → 来自 SubAgent 的协作请求
    """

    def __init__(self, session_id: str) -> None:
        super().__init__(identity=f"pa:{session_id}")
        self._session_id = session_id
        self._pending_notifications: list[dict[str, Any]] = []
        self._current_assistant: Any = None  # 当前正在执行的 PA 实例引用

    # ── 消息分发 ──────────────────────────────────────────

    async def handle_message(self, envelope: Envelope) -> None:
        if envelope.msg_type == MessageType.CHAT_REQUEST:
            await self._handle_chat(envelope)
        elif envelope.msg_type == MessageType.AGENT_NOTIFY:
            await self._handle_notify(envelope)
        elif envelope.msg_type == MessageType.AGENT_REQUEST:
            await self._handle_agent_request(envelope)
        elif envelope.msg_type == MessageType.TOOL_GUARD_RESOLVE:
            await self._handle_tool_guard_resolve(envelope)
        elif envelope.msg_type == MessageType.PLAN_STEP_DONE:
            await self._handle_plan_step_done(envelope)
        elif envelope.msg_type == MessageType.DISPATCH_SUB_ACK:
            # PA 通过 WorkerSchedulerProxy 派遣 SubAgent 后收到的确认
            status = envelope.payload.get("status", "unknown")
            if status == "error":
                logger.error(
                    f"PA daemon [{self._session_id}] SubAgent 派遣失败: "
                    f"{envelope.payload.get('error', '')}"
                )
            else:
                logger.info(f"PA daemon [{self._session_id}] SubAgent 派遣确认: {status}")
        elif envelope.msg_type == MessageType.AGENT_SHUTDOWN:
            logger.info(f"PA daemon [{self._session_id}] 收到关闭信号")
            self._running = False
        else:
            logger.warning(
                f"PA daemon [{self._session_id}] 未知消息类型: {envelope.msg_type}"
            )

    # ── CHAT_REQUEST 处理 ─────────────────────────────────

    async def _handle_chat(self, envelope: Envelope) -> None:
        """处理对话请求：创建 PA → reply_stream() → 逐条发布事件。"""
        from agentpal.agents.personal_assistant import PersonalAssistant
        from agentpal.config import get_settings
        from agentpal.database import AsyncSessionLocal
        from agentpal.memory.factory import MemoryFactory

        user_message = envelope.payload.get("message", "")
        images = envelope.payload.get("images")
        file_ids = envelope.payload.get("file_ids")
        channel = envelope.payload.get("channel", "web")
        msg_id = envelope.msg_id  # 关联 ID，EventSubscriber 用它过滤

        topic = f"session:{self._session_id}"
        settings = get_settings()

        try:
            async with AsyncSessionLocal() as db:
                # 确保 session 记录存在
                await self._ensure_session(db, self._session_id, channel)

                # 创建 PA 实例（每次请求新建，与现有行为一致）
                memory = MemoryFactory.create(settings.memory_backend, db=db)
                assistant = PersonalAssistant(
                    session_id=self._session_id,
                    memory=memory,
                    db=db,
                )
                self._current_assistant = assistant

                # 流式对话
                try:
                    async for event in assistant.reply_stream(
                        user_message,
                        images=images,
                        file_ids=file_ids,
                    ):
                        # 发布事件到 PUB socket
                        await self._publish_sse_event(topic, msg_id, event)

                        # send_file_to_user 成功 → 额外 emit file 事件
                        if (
                            event.get("type") == "tool_done"
                            and event.get("name") == "send_file_to_user"
                            and not event.get("error")
                        ):
                            try:
                                info = json.loads(event.get("output", "{}"))
                                if info.get("status") == "sent":
                                    file_event = {
                                        "type": "file",
                                        "url": info["url"],
                                        "name": info["filename"],
                                        "mime": info.get("mime", "application/octet-stream"),
                                    }
                                    await self._publish_sse_event(topic, msg_id, file_event)
                            except Exception:
                                pass
                finally:
                    self._current_assistant = None

        except Exception as exc:
            logger.error(
                f"PA daemon [{self._session_id}] chat 处理异常: {exc}",
                exc_info=True,
            )
            await self._publish_sse_event(
                topic, msg_id, {"type": "error", "message": str(exc)}
            )

    # ── AGENT_NOTIFY 处理 ────────────────────────────────

    async def _handle_notify(self, envelope: Envelope) -> None:
        """处理 SubAgent / Cron 完成通知。"""
        self._pending_notifications.append(envelope.payload)
        logger.info(
            f"PA daemon [{self._session_id}] 收到通知: "
            f"from={envelope.source} payload_keys={list(envelope.payload.keys())}"
        )

    # ── AGENT_REQUEST 处理 ────────────────────────────────

    async def _handle_agent_request(self, envelope: Envelope) -> None:
        """处理来自 SubAgent 的协作请求。"""
        logger.info(
            f"PA daemon [{self._session_id}] 收到 Agent 请求: "
            f"from={envelope.source}"
        )
        # 回复确认（暂简单处理）
        response = envelope.make_reply(
            msg_type=MessageType.AGENT_RESPONSE,
            payload={"status": "received"},
        )
        await self.send_to_router(response)

    # ── TOOL_GUARD_RESOLVE 处理 ────────────────────────────

    async def _handle_tool_guard_resolve(self, envelope: Envelope) -> None:
        """处理从 API 转发的工具安全确认。"""
        from agentpal.tools.tool_guard import ToolGuardManager

        request_id = envelope.payload.get("request_id", "")
        approved = envelope.payload.get("approved", False)
        guard = ToolGuardManager.get_instance()
        resolved = guard.resolve(request_id, approved)
        if resolved:
            logger.info(
                f"PA daemon [{self._session_id}] tool guard resolved: "
                f"request_id={request_id} approved={approved}"
            )
        else:
            logger.warning(
                f"PA daemon [{self._session_id}] tool guard resolve 未找到: "
                f"request_id={request_id}（可能已过期）"
            )

    # ── PLAN_STEP_DONE 处理 ────────────────────────────────

    async def _handle_plan_step_done(self, envelope: Envelope) -> None:
        """处理计划步骤完成：更新 plan → 推进下一步或完成。"""
        from agentpal.agents.personal_assistant import PersonalAssistant
        from agentpal.config import get_settings
        from agentpal.database import AsyncSessionLocal
        from agentpal.memory.factory import MemoryFactory

        topic = f"session:{self._session_id}"
        msg_id = envelope.msg_id
        settings = get_settings()

        try:
            async with AsyncSessionLocal() as db:
                memory = MemoryFactory.create(settings.memory_backend, db=db)
                assistant = PersonalAssistant(
                    session_id=self._session_id,
                    memory=memory,
                    db=db,
                )
                async for event in assistant.handle_plan_step_done(envelope.payload):
                    await self._publish_sse_event(topic, msg_id, event)
        except Exception as exc:
            logger.error(
                f"PA daemon [{self._session_id}] plan step done 处理异常: {exc}",
                exc_info=True,
            )
            await self._publish_sse_event(
                topic, msg_id, {"type": "error", "message": str(exc)}
            )

    # ── recv_loop override ─────────────────────────────────

    async def _recv_loop(self) -> None:
        """Override: TOOL_GUARD_RESOLVE 绕过队列直接处理，避免死锁。

        _work_loop 串行处理消息，CHAT_REQUEST 处理中 tool guard 的
        event.wait() 会阻塞 work loop。如果 TOOL_GUARD_RESOLVE 也入队，
        就永远等不到被处理 — 死锁。所以在 recv 层直接 resolve。
        """
        assert self._dealer is not None

        while self._running:
            try:
                frames = await self._dealer.recv_multipart()
                if len(frames) < 2:
                    logger.warning(
                        f"AgentDaemon [{self.identity}] 收到格式异常帧: {len(frames)} frames"
                    )
                    continue

                envelope = Envelope.deserialize(frames[-1])

                if envelope.msg_type == MessageType.TOOL_GUARD_RESOLVE:
                    # 直接处理，不入队 — 避免与 CHAT_REQUEST 死锁
                    logger.info(
                        f"[ToolGuard] _recv_loop 拦截到 TOOL_GUARD_RESOLVE: "
                        f"request_id={envelope.payload.get('request_id')} "
                        f"approved={envelope.payload.get('approved')}"
                    )
                    await self._handle_tool_guard_resolve(envelope)
                elif envelope.msg_type == MessageType.CHAT_CANCEL:
                    # 直接处理，不入队 — 与 TOOL_GUARD_RESOLVE 同理
                    logger.info(
                        f"PA daemon [{self._session_id}] 收到 CHAT_CANCEL"
                    )
                    if self._current_assistant is not None:
                        self._current_assistant.cancel()
                    else:
                        logger.info(
                            f"PA daemon [{self._session_id}] CHAT_CANCEL 忽略（无活跃对话）"
                        )
                else:
                    await self._task_queue.put(envelope)

            except zmq.ZMQError as e:
                if e.errno == zmq.ETERM:
                    break
                if self._running:
                    logger.error(f"AgentDaemon [{self.identity}] recv 异常: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.error(f"AgentDaemon [{self.identity}] recv_loop 异常: {e}")

    # ── 辅助方法 ──────────────────────────────────────────

    async def _publish_sse_event(
        self, topic: str, msg_id: str, event: dict[str, Any]
    ) -> None:
        """将 SSE 事件封装为 Envelope 并发布到 PUB socket。"""
        # 在 payload 中嵌入 _msg_id 供 EventSubscriber 过滤
        payload = {**event, "_msg_id": msg_id}
        envelope = Envelope(
            msg_type=MessageType.STREAM_EVENT,
            source=self.identity,
            target="",  # broadcast
            session_id=self._session_id,
            payload=payload,
        )
        await self.publish_event(topic, envelope)

    @staticmethod
    async def _ensure_session(db: Any, session_id: str, channel: str) -> None:
        """Upsert SessionRecord，确保 session 始终出现在列表中。"""
        from datetime import datetime, timezone

        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        from agentpal.models.session import SessionRecord, SessionStatus

        now = datetime.now(timezone.utc)
        stmt = (
            sqlite_insert(SessionRecord)
            .values(
                id=session_id,
                channel=channel,
                status=SessionStatus.ACTIVE,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={"updated_at": now},
            )
        )
        await db.execute(stmt)
        await db.commit()
