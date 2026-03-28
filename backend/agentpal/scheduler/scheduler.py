"""AgentScheduler — Agent 进程调度器。

替代 AgentDaemonManager，将 Agent 从 asyncio Task 升级为独立子进程。
保留 ZMQ 作为 IPC 底层（地址从 inproc:// 切换到 ipc://）。

主要职责：
- 管理 ZMQ broker（ROUTER + XPUB/XSUB）
- 管理 Agent 子进程生命周期（spawn / monitor / reap）
- 消息路由（API → Agent、Agent → Agent）
- 拦截 SubAgent AGENT_RESPONSE，在主进程内投递结果（写 DB + 推 SSE）
- 空闲进程回收
- 状态查询（Dashboard API 用）
"""

from __future__ import annotations

import asyncio
import contextlib
import multiprocessing
import os
import time
from typing import Any

import zmq
import zmq.asyncio
from loguru import logger

from agentpal.scheduler.config import SchedulerConfig
from agentpal.scheduler.state import AgentProcessInfo, AgentState
from agentpal.zmq_bus.protocol import Envelope, MessageType


class ManagedProcess:
    """对 multiprocessing.Process 的轻量包装。"""

    def __init__(self, process: multiprocessing.Process, info: AgentProcessInfo) -> None:
        self.process = process
        self.info = info

    @property
    def is_alive(self) -> bool:
        return self.process.is_alive()

    @property
    def pid(self) -> int | None:
        return self.process.pid


class AgentScheduler:
    """Agent 进程调度器（旧版 inline 模式）。

    .. deprecated::
        使用 ``SchedulerClient`` + ``SchedulerBroker`` 独立进程模式替代。
        此类仍可用于测试（inproc 模式）或向后兼容场景。

    在 FastAPI lifespan 中 start/stop，通过 app.state 供 endpoint 访问。
    兼容 AgentDaemonManager 的接口，实现平滑迁移。
    """

    def __init__(self, config: SchedulerConfig | None = None) -> None:
        self._config = config or SchedulerConfig()
        self._ctx: zmq.asyncio.Context | None = None
        self._router: zmq.asyncio.Socket | None = None
        self._xpub: zmq.asyncio.Socket | None = None
        self._xsub: zmq.asyncio.Socket | None = None

        # 进程注册表
        self._processes: dict[str, ManagedProcess] = {}

        # 后台任务
        self._router_task: asyncio.Task | None = None
        self._event_proxy_task: asyncio.Task | None = None
        self._reaper_task: asyncio.Task | None = None
        self._health_check_task: asyncio.Task | None = None
        self._running = False

        # 用于 daemon PUB 连接的内部 XSUB 地址
        if self._config.events_addr.startswith("ipc://"):
            self._xsub_addr = self._config.events_addr.replace(".sock", "-internal.sock")
        else:
            # inproc 模式
            self._xsub_addr = self._config.events_addr + "-internal"

        # spawn context（macOS 安全 + 避免 fork+asyncio 问题）
        self._mp_ctx = multiprocessing.get_context("spawn")

        # 启动时间（统计用）
        self._started_at: float = 0.0

        # ── 子进程 REGISTER 等待 ──
        self._register_events: dict[str, asyncio.Event] = {}

        # ── 兼容旧 AgentDaemonManager 的 daemon 注册表 ──
        # 在 inproc 模式下仍然使用 daemon 直接管理
        from agentpal.zmq_bus.pa_daemon import PersonalAssistantDaemon
        from agentpal.zmq_bus.sub_daemon import SubAgentDaemon

        self._pa_daemons: dict[str, PersonalAssistantDaemon] = {}
        self._sub_daemons: dict[str, SubAgentDaemon] = {}
        self._cron_daemon: Any = None

        # 检测是否使用 ipc 模式
        self._use_ipc = self._config.router_addr.startswith("ipc://")

    # ── 生命周期 ──────────────────────────────────────────

    async def start(self) -> None:
        """启动 Scheduler：创建 ZMQ broker、后台循环。"""
        self._running = True
        self._started_at = time.time()

        # 清理残留 socket 文件（仅 ipc 模式）
        if self._use_ipc:
            for addr in (self._config.router_addr, self._config.events_addr, self._xsub_addr):
                path = addr.replace("ipc://", "")
                if os.path.exists(path):
                    os.unlink(path)
                    logger.debug(f"清理残留 socket 文件: {path}")

        self._ctx = zmq.asyncio.Context()

        # ROUTER socket — 中心路由
        self._router = self._ctx.socket(zmq.ROUTER)
        self._router.setsockopt(zmq.LINGER, 1000)
        self._router.bind(self._config.router_addr)

        # XPUB socket — 外部事件 broker（SUB 客户端连接此地址）
        self._xpub = self._ctx.socket(zmq.XPUB)
        self._xpub.setsockopt(zmq.LINGER, 1000)
        self._xpub.bind(self._config.events_addr)

        # XSUB socket — 内部接收 daemon PUB 消息
        self._xsub = self._ctx.socket(zmq.XSUB)
        self._xsub.setsockopt(zmq.LINGER, 1000)
        self._xsub.bind(self._xsub_addr)

        # 启动后台任务
        self._router_task = asyncio.create_task(
            self._router_loop(), name="scheduler-router-loop"
        )
        self._event_proxy_task = asyncio.create_task(
            self._event_proxy(), name="scheduler-event-proxy"
        )
        self._reaper_task = asyncio.create_task(
            self._reaper_loop(), name="scheduler-reaper-loop"
        )
        self._health_check_task = asyncio.create_task(
            self._health_check_loop(), name="scheduler-health-check"
        )

        # 启动 CronDaemon
        await self._start_cron_daemon()

        logger.info(
            f"AgentScheduler 已启动 "
            f"(router={self._config.router_addr}, events={self._config.events_addr}, "
            f"use_subprocess={self._config.use_subprocess})"
        )

    async def stop(self) -> None:
        """停止所有子进程 / daemon，关闭 broker。"""
        self._running = False

        # 停止 CronDaemon
        if self._cron_daemon is not None:
            await self._cron_daemon.stop()
            self._cron_daemon = None

        # 停止 daemon（兼容 inproc 模式）
        for daemon in list(self._pa_daemons.values()):
            await daemon.stop()
        self._pa_daemons.clear()

        for daemon in list(self._sub_daemons.values()):
            await daemon.stop()
        self._sub_daemons.clear()

        # 停止子进程（ipc 模式）
        for identity, _managed in list(self._processes.items()):
            await self._stop_process(identity, timeout=5.0)
        self._processes.clear()

        # 清理 register events
        self._register_events.clear()

        # 取消后台任务
        for task in (
            self._router_task,
            self._event_proxy_task,
            self._reaper_task,
            self._health_check_task,
        ):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        # 关闭 sockets
        for sock in (self._router, self._xpub, self._xsub):
            if sock is not None:
                sock.close(linger=0)
        self._router = self._xpub = self._xsub = None

        # 终止 ZMQ context
        if self._ctx is not None:
            self._ctx.term()
            self._ctx = None

        # 清理 socket 文件（ipc 模式）
        if self._use_ipc:
            for addr in (self._config.router_addr, self._config.events_addr, self._xsub_addr):
                path = addr.replace("ipc://", "")
                if os.path.exists(path):
                    with contextlib.suppress(OSError):
                        os.unlink(path)

        logger.info("AgentScheduler 已停止")

    # ── PA 管理 ────────────────────────────────────────────

    async def ensure_pa(self, session_id: str) -> AgentProcessInfo | Any:
        """确保 session 有活跃的 PA，不存在则创建。

        兼容模式：当前仍使用 daemon（inproc），后续切换为子进程（ipc）。
        """
        return await self.ensure_pa_daemon(session_id)

    async def ensure_pa_daemon(self, session_id: str) -> Any:
        """确保指定 session 的 PA daemon 运行中（兼容旧 API）。"""
        from agentpal.zmq_bus.pa_daemon import PersonalAssistantDaemon

        if session_id in self._pa_daemons:
            daemon = self._pa_daemons[session_id]
            if daemon.is_running:
                # 更新/创建对应的 ProcessInfo
                self._ensure_process_info(
                    process_id=f"pa:{session_id}",
                    agent_type="pa",
                    session_id=session_id,
                    state=AgentState.RUNNING,
                )
                return daemon
            del self._pa_daemons[session_id]

        daemon = PersonalAssistantDaemon(session_id)
        await daemon.start(self._ctx, self._config.router_addr, self._xsub_addr)
        self._pa_daemons[session_id] = daemon

        # 创建 ProcessInfo 记录
        self._ensure_process_info(
            process_id=f"pa:{session_id}",
            agent_type="pa",
            session_id=session_id,
            state=AgentState.RUNNING,
        )

        logger.info(f"创建 PA daemon: session={session_id}")
        return daemon

    # ── SubAgent 管理 ──────────────────────────────────────

    async def dispatch_sub_agent(
        self,
        task_id: str,
        task_prompt: str,
        parent_session_id: str,
        agent_name: str = "default",
        model_config: dict | None = None,
        role_prompt: str = "",
        max_tool_rounds: int = 8,
    ) -> AgentProcessInfo:
        """启动 SubAgent 并派发任务。

        根据 use_subprocess 配置选择子进程模式或 inproc daemon 模式。
        """
        process_id = f"sub:{agent_name}:{task_id}"

        if self._config.use_subprocess and self._use_ipc:
            return await self._dispatch_sub_subprocess(
                process_id=process_id,
                task_id=task_id,
                task_prompt=task_prompt,
                parent_session_id=parent_session_id,
                agent_name=agent_name,
                model_config=model_config,
                role_prompt=role_prompt,
                max_tool_rounds=max_tool_rounds,
            )
        else:
            return await self._dispatch_sub_inproc(
                process_id=process_id,
                task_id=task_id,
                task_prompt=task_prompt,
                parent_session_id=parent_session_id,
                agent_name=agent_name,
                model_config=model_config,
                role_prompt=role_prompt,
                max_tool_rounds=max_tool_rounds,
            )

    async def _dispatch_sub_subprocess(
        self,
        process_id: str,
        task_id: str,
        task_prompt: str,
        parent_session_id: str,
        agent_name: str = "default",
        model_config: dict | None = None,
        role_prompt: str = "",
        max_tool_rounds: int = 8,
    ) -> AgentProcessInfo:
        """通过独立子进程启动 SubAgent。"""
        from agentpal.config import get_settings
        from agentpal.scheduler.worker import worker_main

        info = AgentProcessInfo(
            process_id=process_id,
            agent_type="sub_agent",
            session_id=parent_session_id,
            task_id=task_id,
            agent_name=agent_name,
            state=AgentState.PENDING,
        )
        info.transition_to(AgentState.STARTING)

        # 准备子进程参数（都必须可 pickle）
        settings = get_settings()
        config_dict = settings.model_dump()

        process = self._mp_ctx.Process(
            target=worker_main,
            kwargs={
                "identity": process_id,
                "agent_type": "sub_agent",
                "router_addr": self._config.router_addr,
                "events_addr": self._xsub_addr,
                "config_dict": config_dict,
                "agent_name": agent_name,
                "task_id": task_id,
                "model_config": model_config or {},
                "role_prompt": role_prompt,
                "max_tool_rounds": max_tool_rounds,
                "parent_session_id": parent_session_id,
            },
            name=f"sub-agent-{agent_name}-{task_id[:8]}",
            daemon=True,
        )

        # 注册等待事件
        register_event = asyncio.Event()
        self._register_events[process_id] = register_event

        # 启动子进程
        process.start()
        info.os_pid = process.pid
        logger.info(f"SubAgent 子进程已启动: {process_id} pid={process.pid}")

        # 将 ManagedProcess 注册到进程表
        self._processes[process_id] = ManagedProcess(process=process, info=info)

        # 等待 AGENT_REGISTER（子进程连接确认）
        try:
            await self._wait_for_register(process_id, timeout=self._config.process_start_timeout)
        except TimeoutError:
            logger.warning(
                f"SubAgent 子进程注册超时: {process_id}，进程可能仍在启动中"
            )

        # 发送 DISPATCH_TASK 消息
        envelope = Envelope(
            msg_type=MessageType.DISPATCH_TASK,
            source=f"pa:{parent_session_id}",
            target=process_id,
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
        await self._send_to_daemon(process_id, envelope)

        logger.info(f"SubAgent 子进程已派发任务: {process_id}")
        return info

    async def _dispatch_sub_inproc(
        self,
        process_id: str,
        task_id: str,
        task_prompt: str,
        parent_session_id: str,
        agent_name: str = "default",
        model_config: dict | None = None,
        role_prompt: str = "",
        max_tool_rounds: int = 8,
    ) -> AgentProcessInfo:
        """使用 inproc daemon 模式启动 SubAgent（fallback）。"""
        info = AgentProcessInfo(
            process_id=process_id,
            agent_type="sub_agent",
            session_id=parent_session_id,
            task_id=task_id,
            agent_name=agent_name,
            state=AgentState.PENDING,
        )

        await self.create_sub_daemon(
            agent_name=agent_name,
            task_id=task_id,
            task_prompt=task_prompt,
            parent_session_id=parent_session_id,
            model_config=model_config,
            role_prompt=role_prompt,
            max_tool_rounds=max_tool_rounds,
        )

        info.transition_to(AgentState.STARTING)
        info.transition_to(AgentState.IDLE)
        self._processes[process_id] = ManagedProcess(
            process=_DummyProcess(),  # daemon 模式无真实进程
            info=info,
        )

        return info

    async def _wait_for_register(
        self,
        identity: str,
        timeout: float = 15.0,
    ) -> None:
        """等待子进程发送 AGENT_REGISTER。

        Raises:
            TimeoutError: 超时未收到注册消息
        """
        event = self._register_events.get(identity)
        if event is None:
            return

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"SubAgent {identity} 注册超时（{timeout}s）"
            )
        finally:
            # 清理事件
            self._register_events.pop(identity, None)

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
        """创建并启动 SubAgent daemon（兼容旧 API / inproc fallback）。"""
        from agentpal.zmq_bus.sub_daemon import SubAgentDaemon

        identity = f"sub:{agent_name}:{task_id}"

        daemon = SubAgentDaemon(
            agent_name=agent_name or "default",
            task_id=task_id,
        )
        await daemon.start(self._ctx, self._config.router_addr, self._xsub_addr)
        self._sub_daemons[identity] = daemon

        # 注册到 _processes，使 /scheduler/agents 可见
        self._ensure_process_info(
            process_id=identity,
            agent_type="sub_agent",
            state=AgentState.RUNNING,
            session_id=parent_session_id,
            task_id=task_id,
            agent_name=agent_name,
        )

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
            await self._cron_daemon.start(
                self._ctx, self._config.router_addr, self._xsub_addr
            )

            # 创建 ProcessInfo 记录
            self._ensure_process_info(
                process_id="cron:scheduler",
                agent_type="cron",
                state=AgentState.RUNNING,
            )

            logger.info("CronDaemon 已启动")
        except Exception as e:
            logger.error(f"CronDaemon 启动失败: {e}", exc_info=True)

    # ── EventSubscriber 工厂 ──────────────────────────────

    def create_event_subscriber(
        self,
        topic: str,
        filter_msg_id: str | None = None,
    ) -> Any:
        """创建 EventSubscriber 连接到事件 broker。"""
        from agentpal.zmq_bus.event_subscriber import EventSubscriber

        return EventSubscriber(
            ctx=self._ctx,
            events_addr=self._config.events_addr,
            topic=topic,
            filter_msg_id=filter_msg_id,
        )

    # ── 消息发送 ──────────────────────────────────────────

    async def send_to_agent(self, target_identity: str, envelope: Envelope) -> None:
        """通过 ROUTER 路由消息给指定 Agent。"""
        await self._send_to_daemon(target_identity, envelope)

    async def _send_to_daemon(self, target_identity: str, envelope: Envelope) -> None:
        """通过 ROUTER socket 发送消息。"""
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

    # ── 状态查询（Dashboard API 用）──────────────────────

    def list_agents(self) -> list[AgentProcessInfo]:
        """列出所有活跃 Agent 的信息。"""
        result: list[AgentProcessInfo] = []

        # 从 daemon 注册表构建
        for session_id, daemon in self._pa_daemons.items():
            pid = f"pa:{session_id}"
            if pid in self._processes:
                result.append(self._processes[pid].info)
            else:
                info = AgentProcessInfo(
                    process_id=pid,
                    agent_type="pa",
                    session_id=session_id,
                    state=AgentState.RUNNING if daemon.is_running else AgentState.STOPPED,
                    last_active_at=daemon.last_active_at,
                )
                result.append(info)

        for identity, daemon in self._sub_daemons.items():
            if identity in self._processes:
                result.append(self._processes[identity].info)
            else:
                # 解析 identity = "sub:{agent_name}:{task_id}"
                parts = identity.split(":", 2)
                agent_name = parts[1] if len(parts) > 1 else None
                task_id = parts[2] if len(parts) > 2 else None
                info = AgentProcessInfo(
                    process_id=identity,
                    agent_type="sub_agent",
                    state=AgentState.RUNNING if daemon.is_running else AgentState.STOPPED,
                    last_active_at=daemon.last_active_at,
                    session_id=getattr(daemon, "_parent_session_id", None),
                    task_id=task_id,
                    agent_name=agent_name,
                )
                result.append(info)

        # Cron daemon
        if self._cron_daemon is not None:
            cron_pid = "cron:scheduler"
            if cron_pid in self._processes:
                result.append(self._processes[cron_pid].info)
            else:
                info = AgentProcessInfo(
                    process_id=cron_pid,
                    agent_type="cron",
                    state=AgentState.RUNNING if self._cron_daemon.is_running else AgentState.STOPPED,
                    last_active_at=getattr(self._cron_daemon, "last_active_at", time.time()),
                )
                result.append(info)

        # 独立子进程（ipc 模式）
        for pid, managed in self._processes.items():
            if not any(pid == r.process_id for r in result):
                result.append(managed.info)

        return result

    def get_agent(self, identity: str) -> AgentProcessInfo | None:
        """获取指定 Agent 的信息。"""
        if identity in self._processes:
            return self._processes[identity].info
        return None

    def get_stats(self) -> dict:
        """获取聚合统计信息。"""
        agents = self.list_agents()
        by_state: dict[str, int] = {}
        pa_count = 0
        sub_count = 0
        cron_count = 0

        for a in agents:
            state_str = str(a.state)
            by_state[state_str] = by_state.get(state_str, 0) + 1
            if a.agent_type == "pa":
                pa_count += 1
            elif a.agent_type == "sub_agent":
                sub_count += 1
            elif a.agent_type == "cron":
                cron_count += 1

        return {
            "total_processes": len(agents),
            "pa_count": pa_count,
            "sub_agent_count": sub_count,
            "cron_count": cron_count,
            "by_state": by_state,
            "total_memory_mb": 0.0,  # TODO: 通过 psutil 获取
            "uptime_seconds": round(time.time() - self._started_at, 1) if self._started_at else 0,
        }

    # ── 进程停止 ──────────────────────────────────────────

    async def stop_agent(self, identity: str) -> bool:
        """手动停止一个 Agent。"""
        # 先检查 daemon
        if identity.startswith("pa:"):
            session_id = identity[3:]
            if session_id in self._pa_daemons:
                daemon = self._pa_daemons.pop(session_id)
                await daemon.stop()
                if identity in self._processes:
                    self._processes[identity].info.state = AgentState.STOPPED
                logger.info(f"手动停止 PA daemon: {identity}")
                return True

        for ident in list(self._sub_daemons.keys()):
            if ident == identity:
                daemon = self._sub_daemons.pop(ident)
                await daemon.stop()
                if identity in self._processes:
                    self._processes[identity].info.state = AgentState.STOPPED
                logger.info(f"手动停止 SubAgent daemon: {identity}")
                return True

        # 子进程模式
        if identity in self._processes:
            await self._stop_process(identity)
            return True

        return False

    async def _stop_process(self, identity: str, timeout: float = 5.0) -> None:
        """停止一个子进程。"""
        managed = self._processes.get(identity)
        if managed is None:
            return

        with contextlib.suppress(ValueError):
            managed.info.transition_to(AgentState.STOPPING)

        # 发送 SHUTDOWN 消息
        try:
            shutdown_env = Envelope(
                msg_type=MessageType.AGENT_SHUTDOWN,
                source="scheduler",
                target=identity,
            )
            await self.send_to_agent(identity, shutdown_env)
        except Exception:
            pass

        # 等待进程退出
        if managed.process.is_alive():
            managed.process.join(timeout=timeout)
            if managed.process.is_alive():
                managed.process.terminate()
                managed.process.join(timeout=2)

        try:
            managed.info.transition_to(AgentState.STOPPED)
        except ValueError:
            managed.info.state = AgentState.STOPPED

    # ── 内部辅助 ──────────────────────────────────────────

    def _ensure_process_info(
        self,
        process_id: str,
        agent_type: str,
        state: AgentState = AgentState.RUNNING,
        session_id: str | None = None,
        task_id: str | None = None,
        agent_name: str | None = None,
    ) -> AgentProcessInfo:
        """确保 ProcessInfo 存在并更新。"""
        if process_id in self._processes:
            info = self._processes[process_id].info
            info.last_active_at = time.time()
            return info

        info = AgentProcessInfo(
            process_id=process_id,
            agent_type=agent_type,
            state=state,
            session_id=session_id,
            task_id=task_id,
            agent_name=agent_name,
            os_pid=os.getpid(),
        )
        self._processes[process_id] = ManagedProcess(
            process=_DummyProcess(),
            info=info,
        )
        return info

    # ── ROUTER 路由循环 ──────────────────────────────────

    async def _router_loop(self) -> None:
        """ROUTER 接收循环：按 target identity 转发消息。

        拦截 SubAgent 的 AGENT_RESPONSE（source 以 "sub:" 开头），
        在主进程中执行结果投递（写 MemoryRecord + 推 SSE）。
        """
        assert self._router is not None

        while self._running:
            try:
                frames = await self._router.recv_multipart()
                if len(frames) < 3:
                    continue

                envelope_bytes = frames[-1]
                envelope = Envelope.deserialize(envelope_bytes)
                target = envelope.target

                if not target:
                    logger.warning(
                        f"消息无 target: type={envelope.msg_type}"
                    )
                    continue

                # AGENT_REGISTER — 子进程启动确认
                if envelope.msg_type == MessageType.AGENT_REGISTER:
                    await self._handle_agent_register(envelope)
                    continue

                # AGENT_HEARTBEAT — 更新活跃时间
                if envelope.msg_type == MessageType.AGENT_HEARTBEAT:
                    self._handle_heartbeat(envelope)
                    continue

                # DISPATCH_TASK — 创建 SubAgent
                if envelope.msg_type == MessageType.DISPATCH_TASK:
                    await self._handle_dispatch_from_router(envelope)
                    continue

                # AGENT_RESPONSE from SubAgent — 拦截并投递结果到父 Session
                if envelope.msg_type == MessageType.AGENT_RESPONSE:
                    source = envelope.source or ""
                    if source.startswith("sub:"):
                        # 在主进程中投递结果（写 DB + 推 SSE）
                        asyncio.create_task(
                            self._deliver_sub_result(envelope),
                            name=f"deliver-sub-result-{source}",
                        )
                    # 继续转发给 PA daemon（如果有 target）
                    if target and target != "scheduler":
                        await self._send_to_daemon(target, envelope)
                    continue

                # 普通消息：转发到目标
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

    async def _deliver_sub_result(self, envelope: Envelope) -> None:
        """在主进程中将 SubAgent 结果投递到父 Session。

        1. 解析 payload: task_id, status, result, agent_name, parent_session_id
        2. 仅 status == "done" 且有 result 时执行
        3. 写 MemoryRecord 到父 Session
        4. session_event_bus.publish() 推 SSE new_message 事件
        5. 更新 ProcessInfo last_active_at
        """
        payload = envelope.payload
        task_id = payload.get("task_id", "")
        status = payload.get("status", "")
        result = payload.get("result", "")
        agent_name = payload.get("agent_name", "SubAgent")
        source = envelope.source or ""

        # 从 envelope 或 ProcessInfo 获取 parent_session_id
        parent_session_id = envelope.session_id
        if not parent_session_id and source in self._processes:
            parent_session_id = self._processes[source].info.session_id

        # 更新 ProcessInfo 活跃时间
        if source in self._processes:
            self._processes[source].info.last_active_at = time.time()

        # 仅在任务完成且有结果时投递
        if status != "done" or not result or not parent_session_id:
            return

        try:
            import uuid

            from agentpal.database import AsyncSessionLocal
            from agentpal.models.memory import MemoryRecord
            from agentpal.services.session_event_bus import session_event_bus

            display = agent_name or "SubAgent"
            content = (
                f"\U0001f4cb SubAgent\u300c{display}\u300d\u4efb\u52a1\u5b8c\u6210\n\n"
                f"\u4efb\u52a1 ID: {task_id}\n"
                f"\u7ed3\u679c:\n{result[:4000]}"
            )

            async with AsyncSessionLocal() as db:
                record = MemoryRecord(
                    id=str(uuid.uuid4()),
                    session_id=parent_session_id,
                    role="assistant",
                    content=content,
                )
                db.add(record)
                await db.flush()
                await db.commit()

                await session_event_bus.publish(
                    parent_session_id,
                    {
                        "type": "new_message",
                        "message": {
                            "id": record.id,
                            "role": "assistant",
                            "content": content,
                            "created_at": (
                                record.created_at.isoformat()
                                if record.created_at
                                else None
                            ),
                        },
                    },
                )

            logger.info(
                f"SubAgent 结果已投递到 session {parent_session_id} "
                f"(task={task_id}, agent={agent_name})"
            )

        except Exception as e:
            logger.error(
                f"投递 SubAgent 结果失败: task={task_id} error={e}",
                exc_info=True,
            )

    async def _handle_agent_register(self, envelope: Envelope) -> None:
        """处理子进程的 AGENT_REGISTER 消息。"""
        source = envelope.source
        payload = envelope.payload
        agent_type = payload.get("agent_type", "unknown")
        pid = payload.get("pid")

        logger.info(f"Agent 注册: {source} type={agent_type} pid={pid}")

        if source in self._processes:
            info = self._processes[source].info
            info.os_pid = pid
            try:
                info.transition_to(AgentState.IDLE)
            except ValueError:
                info.state = AgentState.IDLE
            info.last_active_at = time.time()

        # 唤醒 _wait_for_register
        register_event = self._register_events.get(source)
        if register_event is not None:
            register_event.set()

    def _handle_heartbeat(self, envelope: Envelope) -> None:
        """更新 Agent 活跃时间。"""
        source = envelope.source
        if source in self._processes:
            self._processes[source].info.last_active_at = time.time()

        # 同步 daemon 的 last_active_at
        for session_id, daemon in self._pa_daemons.items():
            if f"pa:{session_id}" == source:
                daemon.last_active_at = time.time()

    async def _handle_dispatch_from_router(self, envelope: Envelope) -> None:
        """处理从 PA daemon 发来的 DISPATCH_TASK 请求。"""
        payload = envelope.payload
        agent_name = payload.get("agent_name", "default")
        task_id = payload.get("task_id", "")
        task_prompt = payload.get("task_prompt", "")
        parent_session_id = envelope.session_id or ""
        model_config = payload.get("model_config", {})
        role_prompt = payload.get("role_prompt", "")
        max_tool_rounds = payload.get("max_tool_rounds", 8)

        # 使用 dispatch_sub_agent 统一入口（自动选择子进程/inproc）
        await self.dispatch_sub_agent(
            task_id=task_id,
            task_prompt=task_prompt,
            parent_session_id=parent_session_id,
            agent_name=agent_name,
            model_config=model_config,
            role_prompt=role_prompt,
            max_tool_rounds=max_tool_rounds,
        )

    # ── 事件代理（PUB → XPUB）──────────────────────────

    async def _event_proxy(self) -> None:
        """将 daemon PUB 消息从 XSUB 转发到 XPUB。"""
        assert self._xsub is not None
        assert self._xpub is not None

        poller = zmq.asyncio.Poller()
        poller.register(self._xsub, zmq.POLLIN)
        poller.register(self._xpub, zmq.POLLIN)

        while self._running:
            try:
                events = dict(await poller.poll(timeout=1000))

                if self._xsub in events:
                    msg = await self._xsub.recv_multipart()
                    await self._xpub.send_multipart(msg)

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
        """检查空闲 daemon / 进程，超时则回收。"""
        while self._running:
            try:
                await asyncio.sleep(self._config.reaper_interval)
                now = time.time()

                # 回收 PA daemons
                expired_pa = [
                    sid for sid, d in self._pa_daemons.items()
                    if (now - d.last_active_at) > self._config.pa_idle_timeout
                ]
                for sid in expired_pa:
                    daemon = self._pa_daemons.pop(sid)
                    await daemon.stop()
                    pid = f"pa:{sid}"
                    if pid in self._processes:
                        self._processes[pid].info.state = AgentState.STOPPED
                    logger.info(f"回收 PA daemon: session={sid} (空闲超时)")

                # 回收 SubAgent daemons（inproc 模式）
                expired_sub = [
                    ident for ident, d in self._sub_daemons.items()
                    if (now - d.last_active_at) > self._config.sub_idle_timeout
                    or not d.is_running
                ]
                for ident in expired_sub:
                    daemon = self._sub_daemons.pop(ident)
                    if daemon.is_running:
                        await daemon.stop()
                    if ident in self._processes:
                        self._processes[ident].info.state = AgentState.STOPPED
                    logger.info(f"回收 SubAgent daemon: {ident}")

                # 回收 SubAgent 子进程（subprocess 模式）
                for identity, managed in list(self._processes.items()):
                    if not identity.startswith("sub:"):
                        continue
                    if identity in self._sub_daemons:
                        # 已由上面的 inproc 回收处理
                        continue
                    if managed.info.state in (AgentState.STOPPED, AgentState.FAILED):
                        continue
                    # 检查是否是真实子进程（非 DummyProcess）
                    if isinstance(managed.process, _DummyProcess):
                        continue
                    idle_secs = now - managed.info.last_active_at
                    if idle_secs > self._config.sub_idle_timeout:
                        logger.info(
                            f"回收 SubAgent 子进程: {identity} "
                            f"(空闲 {idle_secs:.0f}s > {self._config.sub_idle_timeout}s)"
                        )
                        await self._stop_process(identity, timeout=5.0)
                    elif not managed.process.is_alive():
                        # 进程已退出但状态未更新
                        managed.info.state = AgentState.STOPPED
                        logger.info(f"SubAgent 子进程已退出: {identity}")

                # 清理已停止的 ProcessInfo（保留一段时间供查询）
                stale = [
                    pid for pid, m in self._processes.items()
                    if m.info.state in (AgentState.STOPPED, AgentState.FAILED)
                    and (now - m.info.last_active_at) > 300  # 5 分钟后清理
                ]
                for pid in stale:
                    del self._processes[pid]

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.error(f"Reaper loop 异常: {e}")

    # ── 健康检查循环 ──────────────────────────────────────

    async def _health_check_loop(self) -> None:
        """检查子进程是否存活，dead 的标记 FAILED。"""
        while self._running:
            try:
                await asyncio.sleep(self._config.health_check_interval)

                for identity, managed in list(self._processes.items()):
                    if not managed.info.is_alive:
                        continue

                    # daemon 模式 — 检查 daemon 是否还在运行
                    if identity.startswith("pa:"):
                        session_id = identity[3:]
                        daemon = self._pa_daemons.get(session_id)
                        if daemon and not daemon.is_running:
                            managed.info.state = AgentState.FAILED
                            managed.info.error = "daemon unexpectedly stopped"

                    elif identity in self._sub_daemons:
                        daemon = self._sub_daemons.get(identity)
                        if daemon and not daemon.is_running:
                            managed.info.state = AgentState.STOPPED

                    # 子进程模式 — 检查 Process.is_alive()
                    elif hasattr(managed.process, "is_alive") and not isinstance(managed.process, _DummyProcess):
                        if not managed.process.is_alive():
                            managed.info.state = AgentState.FAILED
                            managed.info.error = "process exited unexpectedly"

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.error(f"Health check 异常: {e}")

    # ── 兼容旧 API ──────────────────────────────────────

    @property
    def pa_daemon_count(self) -> int:
        return len(self._pa_daemons)

    @property
    def sub_daemon_count(self) -> int:
        return len(self._sub_daemons)

    def get_pa_daemon(self, session_id: str) -> Any | None:
        return self._pa_daemons.get(session_id)

    @property
    def zmq_context(self) -> zmq.asyncio.Context | None:
        return self._ctx


class _DummyProcess:
    """daemon 模式下的占位 Process。"""

    def is_alive(self) -> bool:
        return True

    @property
    def pid(self) -> int | None:
        return os.getpid()

    def join(self, timeout: float | None = None) -> None:
        pass

    def terminate(self) -> None:
        pass
