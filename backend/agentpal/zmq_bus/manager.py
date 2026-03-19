"""AgentDaemonManager — 中心路由 + daemon 生命周期管理。

职责：
- 持有全局 zmq.asyncio.Context
- ROUTER socket (inproc://agent-router) 按 identity 路由消息
- XPUB socket (inproc://agent-events) 转发事件
- PA daemon 生命周期管理（per-session，lazy creation，idle 回收）
- SubAgent daemon 生命周期管理（on-demand，idle 回收）
- CronDaemon 单例管理
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import zmq
import zmq.asyncio
from loguru import logger

from agentpal.zmq_bus.protocol import Envelope, MessageType


class AgentDaemonManager:
    """全局 daemon 管理器。

    在 FastAPI lifespan 中 start/stop，通过 app.state 供 endpoint 访问。
    """

    def __init__(
        self,
        router_addr: str = "inproc://agent-router",
        events_addr: str = "inproc://agent-events",
        pa_idle_timeout: int = 1800,
        sub_idle_timeout: int = 300,
    ) -> None:
        self._router_addr = router_addr
        self._events_addr = events_addr
        self._pa_idle_timeout = pa_idle_timeout
        self._sub_idle_timeout = sub_idle_timeout

        self._ctx: zmq.asyncio.Context | None = None
        self._router: zmq.asyncio.Socket | None = None
        self._xpub: zmq.asyncio.Socket | None = None

        # daemon 注册表
        from agentpal.zmq_bus.pa_daemon import PersonalAssistantDaemon
        from agentpal.zmq_bus.sub_daemon import SubAgentDaemon

        self._pa_daemons: dict[str, PersonalAssistantDaemon] = {}   # session_id -> daemon
        self._sub_daemons: dict[str, SubAgentDaemon] = {}            # identity -> daemon
        self._cron_daemon: Any = None

        self._router_task: asyncio.Task | None = None
        self._reaper_task: asyncio.Task | None = None
        self._event_proxy_task: asyncio.Task | None = None
        self._running = False

        # 用于给 daemon PUB 连接的内部 XSUB 地址
        self._xsub_addr = "inproc://agent-events-internal"
        self._xsub: zmq.asyncio.Socket | None = None

    # ── 生命周期 ──────────────────────────────────────────

    async def start(self) -> None:
        """启动 manager：创建 ZMQ context、绑定 sockets、启动后台循环。"""
        self._ctx = zmq.asyncio.Context()
        self._running = True

        # ROUTER socket — 中心路由
        self._router = self._ctx.socket(zmq.ROUTER)
        self._router.setsockopt(zmq.LINGER, 1000)
        self._router.bind(self._router_addr)

        # XPUB socket — 外部事件 broker（SUB 客户端连接此地址）
        self._xpub = self._ctx.socket(zmq.XPUB)
        self._xpub.setsockopt(zmq.LINGER, 1000)
        self._xpub.bind(self._events_addr)

        # XSUB socket — 内部接收 daemon PUB 消息
        self._xsub = self._ctx.socket(zmq.XSUB)
        self._xsub.setsockopt(zmq.LINGER, 1000)
        self._xsub.bind(self._xsub_addr)

        # 启动后台任务
        self._router_task = asyncio.create_task(
            self._router_loop(), name="manager-router-loop"
        )
        self._event_proxy_task = asyncio.create_task(
            self._event_proxy(), name="manager-event-proxy"
        )
        self._reaper_task = asyncio.create_task(
            self._reaper_loop(), name="manager-reaper-loop"
        )

        # 启动 CronDaemon
        await self._start_cron_daemon()

        logger.info(
            f"AgentDaemonManager 已启动 "
            f"(router={self._router_addr}, events={self._events_addr})"
        )

    async def stop(self) -> None:
        """停止所有 daemon 和 manager 自身。"""
        self._running = False

        # 停止 CronDaemon
        if self._cron_daemon is not None:
            await self._cron_daemon.stop()
            self._cron_daemon = None

        # 停止所有 PA daemons
        for daemon in list(self._pa_daemons.values()):
            await daemon.stop()
        self._pa_daemons.clear()

        # 停止所有 SubAgent daemons
        for daemon in list(self._sub_daemons.values()):
            await daemon.stop()
        self._sub_daemons.clear()

        # 取消后台任务
        for task in (self._router_task, self._event_proxy_task, self._reaper_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # 关闭 sockets
        for sock in (self._router, self._xpub, self._xsub):
            if sock is not None:
                sock.close(linger=0)
        self._router = self._xpub = self._xsub = None

        # 终止 ZMQ context
        if self._ctx is not None:
            self._ctx.term()
            self._ctx = None

        logger.info("AgentDaemonManager 已停止")

    # ── PA Daemon 管理 ────────────────────────────────────

    async def ensure_pa_daemon(self, session_id: str) -> Any:
        """确保指定 session 的 PA daemon 运行中，不存在则创建。

        Returns:
            PersonalAssistantDaemon 实例
        """
        from agentpal.zmq_bus.pa_daemon import PersonalAssistantDaemon

        if session_id in self._pa_daemons:
            daemon = self._pa_daemons[session_id]
            if daemon.is_running:
                return daemon
            # daemon 已停止，移除并重建
            del self._pa_daemons[session_id]

        daemon = PersonalAssistantDaemon(session_id)
        await daemon.start(self._ctx, self._router_addr, self._xsub_addr)
        self._pa_daemons[session_id] = daemon
        logger.info(f"创建 PA daemon: session={session_id}")
        return daemon

    # ── SubAgent Daemon 管理 ──────────────────────────────

    async def create_sub_daemon(
        self,
        agent_name: str,
        task_id: str,
        task_prompt: str,
        parent_session_id: str,
        model_config: dict[str, Any] | None = None,
        role_prompt: str = "",
        max_tool_rounds: int = 8,
    ) -> Any:
        """创建并启动 SubAgent daemon。

        Returns:
            SubAgentDaemon 实例
        """
        from agentpal.zmq_bus.sub_daemon import SubAgentDaemon

        identity = f"sub:{agent_name}:{task_id}"

        daemon = SubAgentDaemon(
            agent_name=agent_name or "default",
            task_id=task_id,
        )
        await daemon.start(self._ctx, self._router_addr, self._xsub_addr)
        self._sub_daemons[identity] = daemon

        # 发送 DISPATCH_TASK 消息
        envelope = Envelope(
            msg_type=MessageType.DISPATCH_TASK,
            source=f"pa:{parent_session_id}",
            target=identity,
            session_id=parent_session_id,
            payload={
                "task_id": task_id,
                "task_prompt": task_prompt,
                "parent_session_id": parent_session_id,
                "model_config": model_config or {},
                "role_prompt": role_prompt,
                "max_tool_rounds": max_tool_rounds,
            },
        )
        await self._send_to_daemon(identity, envelope)

        logger.info(f"创建 SubAgent daemon: {identity}")
        return daemon

    # ── CronDaemon ────────────────────────────────────────

    async def _start_cron_daemon(self) -> None:
        """启动 CronDaemon 单例。"""
        try:
            from agentpal.zmq_bus.cron_daemon import CronDaemon

            self._cron_daemon = CronDaemon()
            await self._cron_daemon.start(self._ctx, self._router_addr, self._xsub_addr)
            logger.info("CronDaemon 已启动")
        except Exception as e:
            logger.error(f"CronDaemon 启动失败: {e}", exc_info=True)

    # ── EventSubscriber 工厂 ──────────────────────────────

    def create_event_subscriber(
        self,
        topic: str,
        filter_msg_id: str | None = None,
    ) -> Any:
        """创建一个 EventSubscriber 连接到事件 broker。

        Args:
            topic:          订阅主题（如 "session:abc123"）
            filter_msg_id:  可选，按 msg_id 过滤事件

        Returns:
            EventSubscriber 实例
        """
        from agentpal.zmq_bus.event_subscriber import EventSubscriber

        return EventSubscriber(
            ctx=self._ctx,
            events_addr=self._events_addr,
            topic=topic,
            filter_msg_id=filter_msg_id,
        )

    # ── 消息发送 ──────────────────────────────────────────

    async def send_to_agent(self, target_identity: str, envelope: Envelope) -> None:
        """通过 ROUTER 路由消息给指定 daemon。"""
        await self._send_to_daemon(target_identity, envelope)

    async def _send_to_daemon(self, target_identity: str, envelope: Envelope) -> None:
        """通过 ROUTER socket 发送消息给目标 daemon。

        ROUTER 帧格式：[target_identity, b"", envelope_bytes]
        """
        if self._router is None:
            logger.warning("ROUTER socket 未就绪，丢弃消息")
            return
        try:
            await self._router.send_multipart([
                target_identity.encode("utf-8"),
                b"",
                envelope.serialize(),
            ])
        except zmq.ZMQError as e:
            logger.error(f"发送消息到 {target_identity} 失败: {e}")

    # ── ROUTER 路由循环 ──────────────────────────────────

    async def _router_loop(self) -> None:
        """ROUTER 接收循环：按 target identity 转发消息。

        ROUTER 收到的帧格式：[source_identity, b"", envelope_bytes]
        需要按 envelope.target 转发到目标 daemon。
        """
        assert self._router is not None

        while self._running:
            try:
                frames = await self._router.recv_multipart()
                if len(frames) < 3:
                    continue

                source_identity = frames[0]
                envelope_bytes = frames[-1]
                envelope = Envelope.deserialize(envelope_bytes)

                target = envelope.target
                if not target:
                    logger.warning(f"消息无 target: source={source_identity} type={envelope.msg_type}")
                    continue

                # 特殊处理：DISPATCH_TASK 可能需要创建新 daemon
                if envelope.msg_type == MessageType.DISPATCH_TASK:
                    await self._handle_dispatch_from_router(envelope)
                    continue

                # 正常路由：转发到目标 daemon
                await self._send_to_daemon(target, envelope)

            except zmq.ZMQError as e:
                if e.errno == zmq.ETERM:
                    break
                if self._running:
                    logger.error(f"ROUTER loop 异常: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.error(f"ROUTER loop 异常: {e}", exc_info=True)

    async def _handle_dispatch_from_router(self, envelope: Envelope) -> None:
        """处理从 PA daemon 发来的 DISPATCH_TASK 请求。

        按需创建 SubAgent daemon 并路由任务。
        """
        payload = envelope.payload
        agent_name = payload.get("agent_name", "default")
        task_id = payload.get("task_id", "")
        task_prompt = payload.get("task_prompt", "")
        parent_session_id = envelope.session_id or ""
        model_config = payload.get("model_config", {})
        role_prompt = payload.get("role_prompt", "")
        max_tool_rounds = payload.get("max_tool_rounds", 8)

        await self.create_sub_daemon(
            agent_name=agent_name,
            task_id=task_id,
            task_prompt=task_prompt,
            parent_session_id=parent_session_id,
            model_config=model_config,
            role_prompt=role_prompt,
            max_tool_rounds=max_tool_rounds,
        )

    # ── 事件代理（PUB → XPUB）──────────────────────────

    async def _event_proxy(self) -> None:
        """将 daemon PUB 消息从 XSUB 转发到 XPUB。

        zmq.proxy() 不兼容 asyncio，手动实现转发。
        Daemon 的 PUB connect → XSUB (bind)
        EventSubscriber 的 SUB connect → XPUB (bind)
        """
        assert self._xsub is not None
        assert self._xpub is not None

        poller = zmq.asyncio.Poller()
        poller.register(self._xsub, zmq.POLLIN)
        poller.register(self._xpub, zmq.POLLIN)

        while self._running:
            try:
                events = dict(await poller.poll(timeout=1000))

                # XSUB → XPUB（daemon 发布的事件转发给外部订阅者）
                if self._xsub in events:
                    msg = await self._xsub.recv_multipart()
                    await self._xpub.send_multipart(msg)

                # XPUB → XSUB（订阅/取消订阅请求转发给 daemon PUB）
                if self._xpub in events:
                    msg = await self._xpub.recv_multipart()
                    await self._xsub.send_multipart(msg)

            except zmq.ZMQError as e:
                if e.errno == zmq.ETERM:
                    break
                if self._running:
                    logger.error(f"Event proxy 异常: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.error(f"Event proxy 异常: {e}")

    # ── Idle 回收循环 ────────────────────────────────────

    async def _reaper_loop(self) -> None:
        """每 60 秒检查所有 daemon 的空闲时间，超时则回收。"""
        while self._running:
            try:
                await asyncio.sleep(60)
                now = time.time()

                # 回收 PA daemons
                expired_pa = [
                    sid for sid, d in self._pa_daemons.items()
                    if (now - d.last_active_at) > self._pa_idle_timeout
                ]
                for sid in expired_pa:
                    daemon = self._pa_daemons.pop(sid)
                    await daemon.stop()
                    logger.info(f"回收 PA daemon: session={sid} (空闲超时)")

                # 回收 SubAgent daemons
                expired_sub = [
                    ident for ident, d in self._sub_daemons.items()
                    if (now - d.last_active_at) > self._sub_idle_timeout
                    or not d.is_running
                ]
                for ident in expired_sub:
                    daemon = self._sub_daemons.pop(ident)
                    if daemon.is_running:
                        await daemon.stop()
                    logger.info(f"回收 SubAgent daemon: {ident}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.error(f"Reaper loop 异常: {e}")

    # ── 状态查询 ──────────────────────────────────────────

    @property
    def pa_daemon_count(self) -> int:
        """当前活跃 PA daemon 数量。"""
        return len(self._pa_daemons)

    @property
    def sub_daemon_count(self) -> int:
        """当前活跃 SubAgent daemon 数量。"""
        return len(self._sub_daemons)

    def get_pa_daemon(self, session_id: str) -> Any | None:
        """获取指定 session 的 PA daemon（如果存在）。"""
        return self._pa_daemons.get(session_id)

    @property
    def zmq_context(self) -> zmq.asyncio.Context | None:
        """获取共享的 ZMQ context。"""
        return self._ctx
