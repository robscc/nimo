"""SchedulerBroker — Scheduler 进程内的核心 broker 逻辑。

在独立的 Scheduler 进程中运行，负责：
- ZMQ ROUTER 消息路由
- XSUB→XPUB 事件代理
- 子进程生命周期管理（spawn PA / Cron / SubAgent）
- 空闲进程回收
- 健康检查
- 处理来自 SchedulerClient 的控制消息
- SubAgent 结果拦截与投递
"""

from __future__ import annotations

import asyncio
import contextlib
import multiprocessing
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


class SchedulerBroker:
    """Scheduler 进程核心 broker。

    所有 Agent 一律使用 multiprocessing.Process 启动。
    """

    def __init__(
        self,
        config: SchedulerConfig,
        router_socket: zmq.asyncio.Socket,
        xpub_socket: zmq.asyncio.Socket,
        xsub_socket: zmq.asyncio.Socket,
        xsub_addr: str,
        mp_ctx: Any = None,
    ) -> None:
        self._config = config
        self._router = router_socket
        self._xpub = xpub_socket
        self._xsub = xsub_socket
        self._xsub_addr = xsub_addr

        # spawn context（macOS 安全 + 避免 fork+asyncio 问题）
        self._mp_ctx = mp_ctx or multiprocessing.get_context("spawn")

        # 进程注册表
        self._processes: dict[str, ManagedProcess] = {}

        # 后台任务
        self._router_task: asyncio.Task | None = None
        self._event_proxy_task: asyncio.Task | None = None
        self._reaper_task: asyncio.Task | None = None
        self._health_check_task: asyncio.Task | None = None
        self._running = False

        # 子进程 REGISTER 等待
        self._register_events: dict[str, asyncio.Event] = {}

        # 启动时间
        self._started_at: float = 0.0

    # ── 生命周期 ──────────────────────────────────────────

    async def start(self) -> None:
        """启动 broker 后台循环并自动 spawn Cron 进程。"""
        self._running = True
        self._started_at = time.time()

        # 启动后台任务
        self._router_task = asyncio.create_task(
            self._router_loop(), name="broker-router-loop"
        )
        self._event_proxy_task = asyncio.create_task(
            self._event_proxy(), name="broker-event-proxy"
        )
        self._reaper_task = asyncio.create_task(
            self._reaper_loop(), name="broker-reaper-loop"
        )
        self._health_check_task = asyncio.create_task(
            self._health_check_loop(), name="broker-health-check"
        )

        # 自动 spawn Cron 进程（background task，不阻塞 start()，
        # 因为 _wait_for_register 需要 _router_loop 已运行来处理 AGENT_REGISTER）
        asyncio.create_task(
            self._spawn_cron_process(),
            name="spawn-cron",
        )

        logger.info("SchedulerBroker 已启动")

    async def stop(self) -> None:
        """停止所有子进程，关闭后台循环。"""
        self._running = False

        # 向所有子进程发送 AGENT_SHUTDOWN
        for identity in list(self._processes.keys()):
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

        logger.info("SchedulerBroker 已停止")

    # ── PA 管理 ────────────────────────────────────────────

    async def ensure_pa(self, session_id: str) -> AgentProcessInfo:
        """确保 session 有活跃的 PA 进程，不存在则创建。"""
        process_id = f"pa:{session_id}"

        # 检查是否已存在且活跃
        if process_id in self._processes:
            managed = self._processes[process_id]
            if managed.process.is_alive():
                managed.info.last_active_at = time.time()
                return managed.info
            else:
                # 进程已死，清理后重新创建
                del self._processes[process_id]

        return await self._spawn_pa_process(session_id)

    async def _spawn_pa_process(self, session_id: str) -> AgentProcessInfo:
        """Spawn PA 子进程。"""
        from agentpal.scheduler.worker import worker_main

        process_id = f"pa:{session_id}"
        info = AgentProcessInfo(
            process_id=process_id,
            agent_type="pa",
            session_id=session_id,
            state=AgentState.PENDING,
        )
        info.transition_to(AgentState.STARTING)

        # 注册等待事件
        register_event = asyncio.Event()
        self._register_events[process_id] = register_event

        process = self._mp_ctx.Process(
            target=worker_main,
            kwargs={
                "identity": process_id,
                "agent_type": "pa",
                "router_addr": self._config.router_addr,
                "events_addr": self._xsub_addr,
                "config_dict": {},  # 子进程从 config.yaml 加载
                "session_id": session_id,
            },
            name=f"pa-{session_id[:12]}",
            daemon=True,
        )
        process.start()
        info.os_pid = process.pid

        self._processes[process_id] = ManagedProcess(process=process, info=info)
        logger.info(f"PA 子进程已启动: {process_id} pid={process.pid}")

        # 等待 AGENT_REGISTER
        try:
            await self._wait_for_register(process_id, timeout=self._config.process_start_timeout)
        except TimeoutError:
            logger.warning(f"PA 子进程注册超时: {process_id}，进程可能仍在启动中")

        return info

    # ── Cron 管理 ──────────────────────────────────────────

    async def _spawn_cron_process(self) -> None:
        """Spawn Cron 子进程。"""
        try:
            from agentpal.scheduler.worker import worker_main

            process_id = "cron:scheduler"
            info = AgentProcessInfo(
                process_id=process_id,
                agent_type="cron",
                state=AgentState.PENDING,
            )
            info.transition_to(AgentState.STARTING)

            # 注册等待事件
            register_event = asyncio.Event()
            self._register_events[process_id] = register_event

            process = self._mp_ctx.Process(
                target=worker_main,
                kwargs={
                    "identity": process_id,
                    "agent_type": "cron",
                    "router_addr": self._config.router_addr,
                    "events_addr": self._xsub_addr,
                    "config_dict": {},  # 子进程从 config.yaml 加载
                },
                name="cron-scheduler",
                daemon=True,
            )
            process.start()
            info.os_pid = process.pid

            self._processes[process_id] = ManagedProcess(process=process, info=info)
            logger.info(f"Cron 子进程已启动: pid={process.pid}")

            # 等待 AGENT_REGISTER
            try:
                await self._wait_for_register(process_id, timeout=self._config.process_start_timeout)
            except TimeoutError:
                logger.warning("Cron 子进程注册超时，进程可能仍在启动中")

        except Exception as e:
            logger.error(f"Cron 子进程启动失败: {e}", exc_info=True)

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
        """Spawn SubAgent 子进程并派发任务。"""
        from agentpal.scheduler.worker import worker_main

        process_id = f"sub:{agent_name}:{task_id}"

        info = AgentProcessInfo(
            process_id=process_id,
            agent_type="sub_agent",
            session_id=parent_session_id,
            task_id=task_id,
            agent_name=agent_name,
            state=AgentState.PENDING,
        )
        info.transition_to(AgentState.STARTING)

        # 注册等待事件
        register_event = asyncio.Event()
        self._register_events[process_id] = register_event

        process = self._mp_ctx.Process(
            target=worker_main,
            kwargs={
                "identity": process_id,
                "agent_type": "sub_agent",
                "router_addr": self._config.router_addr,
                "events_addr": self._xsub_addr,
                "config_dict": {},  # 子进程从 config.yaml 加载
                "agent_name": agent_name,
                "task_id": task_id,
                "model_config": model_config or {},
                "role_prompt": role_prompt,
                "max_tool_rounds": max_tool_rounds,
                "parent_session_id": parent_session_id,
            },
            name=f"sub-{agent_name}-{task_id[:8]}",
            daemon=True,
        )
        process.start()
        info.os_pid = process.pid

        self._processes[process_id] = ManagedProcess(process=process, info=info)
        logger.info(f"SubAgent 子进程已启动: {process_id} pid={process.pid}")

        # 等待 AGENT_REGISTER
        try:
            await self._wait_for_register(process_id, timeout=self._config.process_start_timeout)
        except TimeoutError:
            logger.warning(f"SubAgent 子进程注册超时: {process_id}")

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

    # ── 状态查询 ──────────────────────────────────────────

    def list_agents(self) -> list[AgentProcessInfo]:
        """列出所有活跃 Agent 的信息。"""
        return [managed.info for managed in self._processes.values()]

    def get_agent(self, identity: str) -> AgentProcessInfo | None:
        """获取指定 Agent 的信息。"""
        managed = self._processes.get(identity)
        return managed.info if managed else None

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
            "total_memory_mb": 0.0,
            "uptime_seconds": round(time.time() - self._started_at, 1) if self._started_at else 0,
        }

    # ── 进程停止 ──────────────────────────────────────────

    async def stop_agent(self, identity: str) -> bool:
        """手动停止一个 Agent。"""
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
            await self._send_to_daemon(identity, shutdown_env)
        except Exception:
            pass

        # 等待进程退出
        if managed.process.is_alive():
            managed.process.join(timeout=timeout)
            if managed.process.is_alive():
                managed.process.terminate()
                managed.process.join(timeout=2)
                if managed.process.is_alive():
                    managed.process.kill()
                    managed.process.join(timeout=1)

        try:
            managed.info.transition_to(AgentState.STOPPED)
        except ValueError:
            managed.info.state = AgentState.STOPPED

    # ── 消息发送 ──────────────────────────────────────────

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

    async def _wait_for_register(
        self,
        identity: str,
        timeout: float = 15.0,
    ) -> None:
        """等待子进程发送 AGENT_REGISTER。"""
        event = self._register_events.get(identity)
        if event is None:
            return

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"Agent {identity} 注册超时（{timeout}s）") from None
        finally:
            self._register_events.pop(identity, None)

    # ── CONFIG_RELOAD 广播 ────────────────────────────────

    async def broadcast_config_reload(self) -> None:
        """向所有子进程广播 CONFIG_RELOAD 消息。"""
        for identity in list(self._processes.keys()):
            managed = self._processes.get(identity)
            if managed and managed.process.is_alive():
                env = Envelope(
                    msg_type=MessageType.CONFIG_RELOAD,
                    source="scheduler",
                    target=identity,
                )
                await self._send_to_daemon(identity, env)
        logger.info("已广播 CONFIG_RELOAD 到所有子进程")

    # ── ROUTER 路由循环 ──────────────────────────────────

    async def _router_loop(self) -> None:
        """ROUTER 接收循环：按 target identity 转发消息。

        拦截 SubAgent 的 AGENT_RESPONSE（source 以 "sub:" 开头），
        在 Scheduler 进程中执行结果投递（写 DB + 推 SSE）。
        同时处理来自 SchedulerClient 的控制消息。
        """
        assert self._router is not None

        while self._running:
            try:
                frames = await self._router.recv_multipart()
                if len(frames) < 3:
                    continue

                sender_identity = frames[0]
                envelope_bytes = frames[-1]
                envelope = Envelope.deserialize(envelope_bytes)
                target = envelope.target

                # ── Client 控制消息处理 ──
                # 注意：ensure_pa / dispatch_sub / dispatch_from_router
                # 涉及 spawn 子进程 + _wait_for_register，必须以 background task
                # 运行，否则会阻塞 router_loop 导致无法处理 AGENT_REGISTER 消息（死锁）。
                if envelope.msg_type == MessageType.ENSURE_PA:
                    asyncio.create_task(
                        self._handle_ensure_pa(sender_identity, envelope),
                        name=f"ensure-pa-{envelope.payload.get('session_id', '')}",
                    )
                    continue

                if envelope.msg_type == MessageType.DISPATCH_SUB:
                    asyncio.create_task(
                        self._handle_dispatch_sub(sender_identity, envelope),
                        name=f"dispatch-sub-{envelope.msg_id[:8]}",
                    )
                    continue

                if envelope.msg_type == MessageType.LIST_AGENTS:
                    await self._handle_list_agents(sender_identity, envelope)
                    continue

                if envelope.msg_type == MessageType.GET_STATS:
                    await self._handle_get_stats(sender_identity, envelope)
                    continue

                if envelope.msg_type == MessageType.STOP_AGENT_REQ:
                    await self._handle_stop_agent(sender_identity, envelope)
                    continue

                if envelope.msg_type == MessageType.SCHEDULER_SHUTDOWN:
                    logger.info("收到 SCHEDULER_SHUTDOWN，准备关闭")
                    asyncio.create_task(self._graceful_shutdown())
                    continue

                if envelope.msg_type == MessageType.CONFIG_RELOAD:
                    # 刷新本进程的 Settings 缓存
                    from agentpal.config import get_settings
                    get_settings.cache_clear()
                    # 广播到所有子进程
                    await self.broadcast_config_reload()
                    continue

                # ── Agent 生命周期消息 ──
                if envelope.msg_type == MessageType.AGENT_REGISTER:
                    await self._handle_agent_register(envelope)
                    continue

                if envelope.msg_type == MessageType.AGENT_HEARTBEAT:
                    self._handle_heartbeat(envelope)
                    continue

                # ── DISPATCH_TASK — 从 PA daemon 发来的 SubAgent 创建请求 ──
                if envelope.msg_type == MessageType.DISPATCH_TASK:
                    if not target or target == "scheduler":
                        asyncio.create_task(
                            self._handle_dispatch_from_router(envelope),
                            name=f"dispatch-from-router-{envelope.msg_id[:8]}",
                        )
                        continue
                    # 有明确 target 则转发，并标记为 RUNNING
                    self._mark_running(target)
                    await self._send_to_daemon(target, envelope)
                    continue

                # ── AGENT_RESPONSE from SubAgent — 拦截并投递结果 ──
                if envelope.msg_type == MessageType.AGENT_RESPONSE:
                    source = envelope.source or ""
                    # SubAgent 完成 → 标记回 IDLE
                    self._mark_idle(source)
                    if source.startswith("sub:"):
                        asyncio.create_task(
                            self._deliver_sub_result(envelope),
                            name=f"deliver-sub-result-{source}",
                        )
                    if target and target != "scheduler":
                        await self._send_to_daemon(target, envelope)
                    continue

                # ── 普通消息：转发到目标 ──
                if target and target != "scheduler":
                    # CHAT_REQUEST 等工作消息 → 标记为 RUNNING
                    if envelope.msg_type == MessageType.CHAT_REQUEST:
                        self._mark_running(target)
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

    # ── 控制消息处理器 ────────────────────────────────────

    async def _handle_ensure_pa(self, sender: bytes, envelope: Envelope) -> None:
        """处理 ENSURE_PA 请求。"""
        session_id = envelope.payload.get("session_id", "")
        try:
            info = await self.ensure_pa(session_id)
            reply = Envelope(
                msg_type=MessageType.ENSURE_PA_ACK,
                source="scheduler",
                target=envelope.source,
                reply_to=envelope.msg_id,
                session_id=session_id,
                payload={"status": "ok", "process_info": info.to_dict()},
            )
        except Exception as e:
            reply = Envelope(
                msg_type=MessageType.ENSURE_PA_ACK,
                source="scheduler",
                target=envelope.source,
                reply_to=envelope.msg_id,
                session_id=session_id,
                payload={"status": "error", "error": str(e)},
            )
        await self._router.send_multipart([
            sender, b"", reply.serialize(),
        ])

    async def _handle_dispatch_sub(self, sender: bytes, envelope: Envelope) -> None:
        """处理 DISPATCH_SUB 请求。"""
        p = envelope.payload
        try:
            info = await self.dispatch_sub_agent(
                task_id=p.get("task_id", ""),
                task_prompt=p.get("task_prompt", ""),
                parent_session_id=p.get("parent_session_id", ""),
                agent_name=p.get("agent_name", "default"),
                model_config=p.get("model_config"),
                role_prompt=p.get("role_prompt", ""),
                max_tool_rounds=p.get("max_tool_rounds", 8),
            )
            reply = Envelope(
                msg_type=MessageType.DISPATCH_SUB_ACK,
                source="scheduler",
                target=envelope.source,
                reply_to=envelope.msg_id,
                payload={"status": "ok", "process_info": info.to_dict()},
            )
        except Exception as e:
            reply = Envelope(
                msg_type=MessageType.DISPATCH_SUB_ACK,
                source="scheduler",
                target=envelope.source,
                reply_to=envelope.msg_id,
                payload={"status": "error", "error": str(e)},
            )
        await self._router.send_multipart([
            sender, b"", reply.serialize(),
        ])

    async def _handle_list_agents(self, sender: bytes, envelope: Envelope) -> None:
        """处理 LIST_AGENTS 请求。"""
        agents = self.list_agents()
        reply = Envelope(
            msg_type=MessageType.LIST_AGENTS_RESP,
            source="scheduler",
            target=envelope.source,
            reply_to=envelope.msg_id,
            payload={"agents": [a.to_dict() for a in agents]},
        )
        await self._router.send_multipart([
            sender, b"", reply.serialize(),
        ])

    async def _handle_get_stats(self, sender: bytes, envelope: Envelope) -> None:
        """处理 GET_STATS 请求。"""
        stats = self.get_stats()
        reply = Envelope(
            msg_type=MessageType.GET_STATS_RESP,
            source="scheduler",
            target=envelope.source,
            reply_to=envelope.msg_id,
            payload={"stats": stats},
        )
        await self._router.send_multipart([
            sender, b"", reply.serialize(),
        ])

    async def _handle_stop_agent(self, sender: bytes, envelope: Envelope) -> None:
        """处理 STOP_AGENT_REQ 请求。"""
        identity = envelope.payload.get("identity", "")
        success = await self.stop_agent(identity)
        reply = Envelope(
            msg_type=MessageType.STOP_AGENT_RESP,
            source="scheduler",
            target=envelope.source,
            reply_to=envelope.msg_id,
            payload={"success": success, "identity": identity},
        )
        await self._router.send_multipart([
            sender, b"", reply.serialize(),
        ])

    async def _graceful_shutdown(self) -> None:
        """优雅关闭 — 停止 broker 后设置全局 shutdown event。"""
        await self.stop()
        # 设置 shutdown event（由 process.py 监听）
        # 直接让事件循环停止
        loop = asyncio.get_running_loop()
        loop.call_soon(loop.stop)

    # ── Agent 生命周期处理 ────────────────────────────────

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

    def _mark_running(self, identity: str) -> None:
        """将 Agent 标记为 RUNNING（正在处理消息）。"""
        if identity in self._processes:
            info = self._processes[identity].info
            if info.state == AgentState.IDLE:
                try:
                    info.transition_to(AgentState.RUNNING)
                except ValueError:
                    pass

    def _mark_idle(self, identity: str) -> None:
        """将 Agent 标记回 IDLE（消息处理完毕）。"""
        if identity in self._processes:
            info = self._processes[identity].info
            if info.state == AgentState.RUNNING:
                try:
                    info.transition_to(AgentState.IDLE)
                except ValueError:
                    pass

    async def _handle_dispatch_from_router(self, envelope: Envelope) -> None:
        """处理从 PA daemon 发来的 DISPATCH_TASK 请求。"""
        payload = envelope.payload
        await self.dispatch_sub_agent(
            task_id=payload.get("task_id", ""),
            task_prompt=payload.get("task_prompt", ""),
            parent_session_id=envelope.session_id or "",
            agent_name=payload.get("agent_name", "default"),
            model_config=payload.get("model_config", {}),
            role_prompt=payload.get("role_prompt", ""),
            max_tool_rounds=payload.get("max_tool_rounds", 8),
        )

    # ── SubAgent 结果投递 ────────────────────────────────

    async def _deliver_sub_result(self, envelope: Envelope) -> None:
        """将 SubAgent 结果投递到父 Session（写 DB + 通过 ZMQ 推 SSE）。"""
        payload = envelope.payload
        task_id = payload.get("task_id", "")
        status = payload.get("status", "")
        result = payload.get("result", "")
        agent_name = payload.get("agent_name", "SubAgent")
        source = envelope.source or ""

        # 获取 parent_session_id
        parent_session_id = envelope.session_id
        if not parent_session_id and source in self._processes:
            parent_session_id = self._processes[source].info.session_id

        # 更新 ProcessInfo 活跃时间
        if source in self._processes:
            self._processes[source].info.last_active_at = time.time()

        if status != "done" or not result or not parent_session_id:
            return

        try:
            import uuid
            from datetime import datetime, timezone

            from agentpal.database import AsyncSessionLocal
            from agentpal.models.memory import MemoryRecord
            from agentpal.zmq_bus.protocol import Envelope as Env
            from agentpal.zmq_bus.protocol import MessageType

            display = agent_name or "SubAgent"
            content = result[:4000]
            meta = {
                "card_type": "sub_agent_result",
                "agent_name": display,
                "task_id": task_id,
            }

            async with AsyncSessionLocal() as db:
                record = MemoryRecord(
                    id=str(uuid.uuid4()),
                    session_id=parent_session_id,
                    role="assistant",
                    content=content,
                    meta=meta,
                )
                db.add(record)
                await db.flush()
                await db.commit()

                # 通过 XPUB 发布 STREAM_EVENT 供前端 SSE 实时推送
                # （不使用 session_event_bus，因为它是进程内 pub/sub，
                #   Scheduler 进程发布的事件 FastAPI 进程收不到）
                created_at = datetime.now(timezone.utc).isoformat()
                topic = f"session:{parent_session_id}"
                event_envelope = Env(
                    msg_type=MessageType.STREAM_EVENT,
                    source="scheduler",
                    target="",
                    session_id=parent_session_id,
                    payload={
                        "type": "new_message",
                        "message": {
                            "id": record.id,
                            "role": "assistant",
                            "content": content,
                            "created_at": created_at,
                            "meta": meta,
                        },
                    },
                )
                # 发送到 XPUB：[topic_bytes, envelope_bytes]
                topic_bytes = topic.encode("utf-8")
                event_bytes = event_envelope.serialize()
                await self._xpub.send_multipart([topic_bytes, event_bytes])

            logger.info(
                f"SubAgent 结果已投递到 session {parent_session_id} "
                f"(task={task_id}, agent={agent_name})"
            )

        except Exception as e:
            logger.error(
                f"投递 SubAgent 结果失败: task={task_id} error={e}",
                exc_info=True,
            )

    # ── 事件代理（PUB → XPUB）──────────────────────────

    async def _event_proxy(self) -> None:
        """将 daemon PUB 消息从 XSUB 转发到 XPUB。

        同时拦截 STREAM_EVENT(type=done) 将 PA 标记回 IDLE。
        """
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

                    # 拦截 done 事件 → 将 PA 标记回 IDLE
                    if len(msg) >= 2:
                        try:
                            envelope = Envelope.deserialize(msg[-1])
                            if (
                                envelope.msg_type == MessageType.STREAM_EVENT
                                and envelope.payload.get("type") == "done"
                            ):
                                source = envelope.source or ""
                                self._mark_idle(source)
                        except Exception:
                            pass  # 解析失败不影响转发

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
        """检查空闲进程，超时则回收。"""
        while self._running:
            try:
                await asyncio.sleep(self._config.reaper_interval)
                now = time.time()

                for identity, managed in list(self._processes.items()):
                    if managed.info.state in (AgentState.STOPPED, AgentState.FAILED):
                        continue

                    # 确定超时时间
                    if identity.startswith("pa:"):
                        idle_timeout = self._config.pa_idle_timeout
                    elif identity.startswith("sub:"):
                        idle_timeout = self._config.sub_idle_timeout
                    else:
                        continue  # Cron 不回收

                    # 检查进程是否已退出
                    if not managed.process.is_alive():
                        managed.info.state = AgentState.STOPPED
                        logger.info(f"子进程已退出: {identity}")
                        continue

                    # 检查空闲超时
                    idle_secs = now - managed.info.last_active_at
                    if idle_secs > idle_timeout:
                        logger.info(
                            f"回收子进程: {identity} "
                            f"(空闲 {idle_secs:.0f}s > {idle_timeout}s)"
                        )
                        await self._stop_process(identity, timeout=5.0)

                # 清理已停止的 ProcessInfo（保留 5 分钟供查询）
                stale = [
                    pid for pid, m in self._processes.items()
                    if m.info.state in (AgentState.STOPPED, AgentState.FAILED)
                    and (now - m.info.last_active_at) > 300
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

                    if not managed.process.is_alive():
                        managed.info.state = AgentState.FAILED
                        managed.info.error = "process exited unexpectedly"
                        logger.warning(f"子进程异常退出: {identity}")

                        # 自动重启 Cron 进程
                        if identity == "cron:scheduler" and self._config.cron_auto_restart:
                            logger.info("尝试自动重启 Cron 子进程...")
                            del self._processes[identity]
                            await self._spawn_cron_process()

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.error(f"Health check 异常: {e}")
