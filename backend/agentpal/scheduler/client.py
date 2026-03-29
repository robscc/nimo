"""SchedulerClient — FastAPI 进程中的 Scheduler 薄客户端。

运行在 FastAPI 主进程中，通过 ZMQ DEALER socket 与独立的 Scheduler 进程通信。
实现与 AgentScheduler 完全兼容的公共接口，确保 API 层零修改。

架构：
  FastAPI Process (PID A)
    └── SchedulerClient
          ├── DEALER socket → 连接 Scheduler ROUTER
          ├── create_event_subscriber() → SUB socket → Scheduler XPUB
          └── 管理 Scheduler 进程生命周期 (multiprocessing.Process)

  Scheduler Process (PID B)
    └── SchedulerBroker
          ├── ROUTER socket (bind ipc://)
          ├── XPUB/XSUB event proxy (bind ipc://)
          └── spawn PA / Cron / SubAgent 子进程
"""

from __future__ import annotations

import asyncio
import atexit
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


class SchedulerClient:
    """FastAPI 进程中的 Scheduler 薄客户端。

    接口与 AgentScheduler 完全兼容。
    """

    def __init__(self, config: SchedulerConfig | None = None) -> None:
        self._config = config or SchedulerConfig()
        self._ctx: zmq.asyncio.Context | None = None
        self._dealer: zmq.asyncio.Socket | None = None

        # Scheduler 进程
        self._scheduler_process: multiprocessing.Process | None = None
        self._ready_event: multiprocessing.Event | None = None
        self._mp_ctx = multiprocessing.get_context("spawn")

        # 运行状态
        self._running = False
        self._started_at: float = 0.0

        # 缓存的 agents / stats（后台定期刷新）
        self._cached_agents: list[dict] = []
        self._cached_stats: dict = {}
        self._cache_refresh_task: asyncio.Task | None = None

        # 请求-响应关联
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._recv_task: asyncio.Task | None = None

        # 健康检查
        self._health_check_task: asyncio.Task | None = None

        # Client identity
        self._identity = "client:fastapi"

    # ── 生命周期 ──────────────────────────────────────────

    async def start(self) -> None:
        """启动 SchedulerClient：

        1. 清理残留 socket 文件
        2. Spawn Scheduler 进程
        3. 等待 ready_event
        4. 连接 DEALER socket
        5. 启动后台任务
        """
        self._running = True
        self._started_at = time.time()

        # 创建 ZMQ context
        self._ctx = zmq.asyncio.Context()

        # Spawn Scheduler 进程
        await self._spawn_scheduler()

        # 连接 DEALER socket
        self._dealer = self._ctx.socket(zmq.DEALER)
        self._dealer.setsockopt(zmq.IDENTITY, self._identity.encode("utf-8"))
        self._dealer.setsockopt(zmq.LINGER, 1000)
        self._dealer.connect(self._config.router_addr)

        # 短暂等待连接建立
        await asyncio.sleep(0.1)

        # 启动接收循环
        self._recv_task = asyncio.create_task(
            self._recv_loop(), name="scheduler-client-recv"
        )

        # 启动缓存刷新
        self._cache_refresh_task = asyncio.create_task(
            self._cache_refresh_loop(), name="scheduler-client-cache"
        )

        # 启动健康检查
        self._health_check_task = asyncio.create_task(
            self._health_check_loop(), name="scheduler-client-health"
        )

        # 注册 atexit 回调（确保异常退出也能清理）
        atexit.register(self._atexit_cleanup)

        logger.info(
            f"SchedulerClient 已启动 "
            f"(scheduler_pid={self._scheduler_process.pid if self._scheduler_process else 'N/A'})"
        )

    async def stop(self) -> None:
        """停止 SchedulerClient 及 Scheduler 进程。"""
        self._running = False

        # 取消后台任务
        for task in (self._recv_task, self._cache_refresh_task, self._health_check_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        # 清理 pending requests
        for fut in self._pending_requests.values():
            if not fut.done():
                fut.cancel()
        self._pending_requests.clear()

        # 发送 SCHEDULER_SHUTDOWN 消息
        if self._dealer is not None:
            try:
                shutdown_env = Envelope(
                    msg_type=MessageType.SCHEDULER_SHUTDOWN,
                    source=self._identity,
                    target="scheduler",
                )
                await self._dealer.send_multipart([b"", shutdown_env.serialize()])
                logger.info("已发送 SCHEDULER_SHUTDOWN 到 Scheduler 进程")
            except zmq.ZMQError:
                pass

        # 等待 Scheduler 进程退出
        await self._join_scheduler(timeout=10.0)

        # 关闭 DEALER socket
        if self._dealer is not None:
            self._dealer.close(linger=0)
            self._dealer = None

        # 终止 ZMQ context
        if self._ctx is not None:
            self._ctx.term()
            self._ctx = None

        logger.info("SchedulerClient 已停止")

    # ── PA 管理（兼容 AgentScheduler 接口）────────────────

    async def ensure_pa(self, session_id: str) -> Any:
        """确保 session 有活跃的 PA 进程。"""
        return await self.ensure_pa_daemon(session_id)

    async def ensure_pa_daemon(self, session_id: str) -> Any:
        """确保指定 session 的 PA 运行中（兼容旧 API）。"""
        resp = await self._request(
            MessageType.ENSURE_PA,
            payload={"session_id": session_id},
            expected_reply=MessageType.ENSURE_PA_ACK,
            timeout=self._config.process_start_timeout + 5,
        )
        if resp and resp.payload.get("status") == "error":
            raise RuntimeError(resp.payload.get("error", "ensure_pa failed"))
        return resp

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
    ) -> Any:
        """派遣 SubAgent。"""
        resp = await self._request(
            MessageType.DISPATCH_SUB,
            payload={
                "task_id": task_id,
                "task_prompt": task_prompt,
                "parent_session_id": parent_session_id,
                "agent_name": agent_name,
                "model_config": model_config or {},
                "role_prompt": role_prompt,
                "max_tool_rounds": max_tool_rounds,
            },
            expected_reply=MessageType.DISPATCH_SUB_ACK,
            timeout=self._config.process_start_timeout + 5,
        )
        if resp and resp.payload.get("status") == "error":
            raise RuntimeError(resp.payload.get("error", "dispatch_sub_agent failed"))

        # 返回 AgentProcessInfo（从响应重建）
        if resp and resp.payload.get("process_info"):
            info_dict = resp.payload["process_info"]
            return AgentProcessInfo(
                process_id=info_dict.get("process_id", ""),
                agent_type=info_dict.get("agent_type", "sub_agent"),
                state=AgentState(info_dict.get("state", "pending")),
                session_id=info_dict.get("session_id"),
                task_id=info_dict.get("task_id"),
                agent_name=info_dict.get("agent_name"),
                os_pid=info_dict.get("os_pid"),
            )
        return resp

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
        """创建 SubAgent（兼容旧 API，实际走 dispatch_sub_agent）。"""
        return await self.dispatch_sub_agent(
            task_id=task_id,
            task_prompt=task_prompt,
            parent_session_id=parent_session_id,
            agent_name=agent_name,
            model_config=model_config,
            role_prompt=role_prompt,
            max_tool_rounds=max_tool_rounds,
        )

    # ── 消息发送（兼容 AgentScheduler 接口）──────────────

    async def send_to_agent(self, target_identity: str, envelope: Envelope) -> None:
        """通过 DEALER socket 转发消息到 Scheduler ROUTER。"""
        if self._dealer is None:
            logger.warning("DEALER socket 未就绪，丢弃消息")
            return
        try:
            logger.info(
                f"[ToolGuard] SchedulerClient.send_to_agent: "
                f"target={target_identity} msg_type={envelope.msg_type}"
            )
            await self._dealer.send_multipart([b"", envelope.serialize()])
        except zmq.ZMQError as e:
            logger.error(f"发送消息到 {target_identity} 失败: {e}")

    # ── EventSubscriber 工厂 ──────────────────────────────

    def create_event_subscriber(
        self,
        topic: str,
        filter_msg_id: str | None = None,
    ) -> Any:
        """创建 EventSubscriber 连接到 Scheduler 的 XPUB。"""
        from agentpal.zmq_bus.event_subscriber import EventSubscriber

        return EventSubscriber(
            ctx=self._ctx,
            events_addr=self._config.events_addr,
            topic=topic,
            filter_msg_id=filter_msg_id,
        )

    # ── 状态查询 ──────────────────────────────────────────

    def list_agents(self) -> list[AgentProcessInfo]:
        """列出所有活跃 Agent 的信息（使用缓存）。"""
        return [
            AgentProcessInfo(
                process_id=a.get("process_id", ""),
                agent_type=a.get("agent_type", ""),
                state=AgentState(a.get("state", "pending")),
                session_id=a.get("session_id"),
                task_id=a.get("task_id"),
                agent_name=a.get("agent_name"),
                os_pid=a.get("os_pid"),
            )
            for a in self._cached_agents
        ]

    def get_agent(self, identity: str) -> AgentProcessInfo | None:
        """获取指定 Agent 的信息（使用缓存）。"""
        for a in self._cached_agents:
            if a.get("process_id") == identity:
                return AgentProcessInfo(
                    process_id=a.get("process_id", ""),
                    agent_type=a.get("agent_type", ""),
                    state=AgentState(a.get("state", "pending")),
                    session_id=a.get("session_id"),
                    task_id=a.get("task_id"),
                    agent_name=a.get("agent_name"),
                    os_pid=a.get("os_pid"),
                )
        return None

    def get_stats(self) -> dict:
        """获取聚合统计信息（使用缓存）。"""
        if self._cached_stats:
            return self._cached_stats
        return {
            "total_processes": 0,
            "pa_count": 0,
            "sub_agent_count": 0,
            "cron_count": 0,
            "by_state": {},
            "total_memory_mb": 0.0,
            "uptime_seconds": round(time.time() - self._started_at, 1) if self._started_at else 0,
        }

    async def stop_agent(self, identity: str) -> bool:
        """手动停止一个 Agent。"""
        resp = await self._request(
            MessageType.STOP_AGENT_REQ,
            payload={"identity": identity},
            expected_reply=MessageType.STOP_AGENT_RESP,
            timeout=10,
        )
        if resp:
            return resp.payload.get("success", False)
        return False

    # ── 配置同步 ──────────────────────────────────────────

    async def broadcast_config_reload(self) -> None:
        """广播 CONFIG_RELOAD 到 Scheduler 进程（由其转发到所有子进程）。"""
        if self._dealer is None:
            logger.warning("DEALER socket 未就绪，无法广播 CONFIG_RELOAD")
            return
        try:
            env = Envelope(
                msg_type=MessageType.CONFIG_RELOAD,
                source=self._identity,
                target="scheduler",
            )
            await self._dealer.send_multipart([b"", env.serialize()])
            logger.info("已发送 CONFIG_RELOAD 到 Scheduler 进程")
        except zmq.ZMQError as e:
            logger.error(f"广播 CONFIG_RELOAD 失败: {e}")

    # ── 兼容旧 API ──────────────────────────────────────

    @property
    def pa_daemon_count(self) -> int:
        return sum(1 for a in self._cached_agents if a.get("agent_type") == "pa")

    @property
    def sub_daemon_count(self) -> int:
        return sum(1 for a in self._cached_agents if a.get("agent_type") == "sub_agent")

    def get_pa_daemon(self, session_id: str) -> Any | None:
        """兼容旧 API — 返回 None（子进程模式下无 daemon 对象）。"""
        return None

    @property
    def zmq_context(self) -> zmq.asyncio.Context | None:
        return self._ctx

    # ── 内部通信 ──────────────────────────────────────────

    async def _request(
        self,
        msg_type: MessageType,
        payload: dict[str, Any],
        expected_reply: MessageType,
        timeout: float = 10.0,
    ) -> Envelope | None:
        """发送请求并等待响应。"""
        if self._dealer is None:
            logger.warning("DEALER socket 未就绪")
            return None

        env = Envelope(
            msg_type=msg_type,
            source=self._identity,
            target="scheduler",
            payload=payload,
        )

        # 注册 Future
        fut: asyncio.Future[Envelope] = asyncio.get_running_loop().create_future()
        self._pending_requests[env.msg_id] = fut

        try:
            await self._dealer.send_multipart([b"", env.serialize()])

            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"请求超时: {msg_type} (timeout={timeout}s)")
            return None
        except asyncio.CancelledError:
            return None
        finally:
            self._pending_requests.pop(env.msg_id, None)

    async def _recv_loop(self) -> None:
        """接收 Scheduler 响应并分发到 pending requests。"""
        assert self._dealer is not None

        while self._running:
            try:
                frames = await self._dealer.recv_multipart()
                if len(frames) < 2:
                    continue

                envelope = Envelope.deserialize(frames[-1])

                # 检查是否有对应的 pending request
                reply_to = envelope.reply_to
                if reply_to and reply_to in self._pending_requests:
                    fut = self._pending_requests.pop(reply_to)
                    if not fut.done():
                        fut.set_result(envelope)

            except zmq.ZMQError as e:
                if e.errno == zmq.ETERM:
                    break
                if self._running:
                    logger.error(f"Client recv 异常: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.error(f"Client recv_loop 异常: {e}")

    async def _cache_refresh_loop(self) -> None:
        """定期刷新 agents / stats 缓存。"""
        while self._running:
            try:
                await asyncio.sleep(5)  # 每 5 秒刷新一次

                # 刷新 agents
                resp = await self._request(
                    MessageType.LIST_AGENTS,
                    payload={},
                    expected_reply=MessageType.LIST_AGENTS_RESP,
                    timeout=5,
                )
                if resp:
                    self._cached_agents = resp.payload.get("agents", [])

                # 刷新 stats
                resp = await self._request(
                    MessageType.GET_STATS,
                    payload={},
                    expected_reply=MessageType.GET_STATS_RESP,
                    timeout=5,
                )
                if resp:
                    self._cached_stats = resp.payload.get("stats", {})

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.warning(f"缓存刷新失败: {e}")

    # ── Scheduler 进程管理 ────────────────────────────────

    async def _spawn_scheduler(self) -> None:
        """Spawn Scheduler 进程并等待就绪。"""
        from agentpal.scheduler.process import scheduler_process_main

        self._ready_event = self._mp_ctx.Event()

        # daemon=False：Scheduler 需要 spawn PA/Cron/SubAgent 子进程，
        # Python 禁止 daemon 进程创建子进程。
        # 生命周期由 SchedulerClient.stop() + atexit 回调显式管理。
        self._scheduler_process = self._mp_ctx.Process(
            target=scheduler_process_main,
            kwargs={
                "router_addr": self._config.router_addr,
                "events_addr": self._config.events_addr,
                "config_dict": {},  # 子进程从 config.yaml 加载
                "ready_event": self._ready_event,
            },
            name="agentpal-scheduler",
            daemon=False,
        )
        self._scheduler_process.start()
        logger.info(f"Scheduler 进程已启动: pid={self._scheduler_process.pid}")

        # 等待 ready_event（带超时）
        start_timeout = getattr(self._config, "scheduler_start_timeout", 30)
        start_time = time.time()
        while not self._ready_event.is_set():
            if time.time() - start_time > start_timeout:
                logger.error("Scheduler 进程启动超时")
                break
            await asyncio.sleep(0.1)

        if self._ready_event.is_set():
            logger.info("Scheduler 进程已就绪")
        else:
            logger.warning("Scheduler 进程可能仍在启动中")

    async def _join_scheduler(self, timeout: float = 10.0) -> None:
        """等待 Scheduler 进程退出。"""
        if self._scheduler_process is None:
            return

        # 在线程中 join（避免阻塞 event loop）
        loop = asyncio.get_running_loop()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(
                loop.run_in_executor(None, self._scheduler_process.join, timeout),
                timeout=timeout + 2,
            )

        if self._scheduler_process.is_alive():
            logger.warning("Scheduler 进程 join 超时，发送 terminate")
            self._scheduler_process.terminate()

            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    loop.run_in_executor(None, self._scheduler_process.join, 2),
                    timeout=3,
                )

            if self._scheduler_process.is_alive():
                logger.error("Scheduler 进程仍存活，发送 kill")
                self._scheduler_process.kill()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        loop.run_in_executor(None, self._scheduler_process.join, 1),
                        timeout=2,
                    )

        self._scheduler_process = None

    async def _health_check_loop(self) -> None:
        """检测 Scheduler 进程是否存活。"""
        while self._running:
            try:
                await asyncio.sleep(10)

                if self._scheduler_process and not self._scheduler_process.is_alive():
                    logger.error("Scheduler 进程已退出，尝试重启...")
                    await self._spawn_scheduler()

                    # 重新连接 DEALER
                    if self._dealer is not None:
                        self._dealer.close(linger=0)
                    self._dealer = self._ctx.socket(zmq.DEALER)
                    self._dealer.setsockopt(zmq.IDENTITY, self._identity.encode("utf-8"))
                    self._dealer.setsockopt(zmq.LINGER, 1000)
                    self._dealer.connect(self._config.router_addr)
                    await asyncio.sleep(0.1)

                    logger.info("Scheduler 进程已重启")

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.error(f"Health check 异常: {e}")

    def _atexit_cleanup(self) -> None:
        """atexit 回调：确保异常退出时也能清理子进程。"""
        if self._scheduler_process and self._scheduler_process.is_alive():
            logger.info("atexit: 终止 Scheduler 子进程")
            self._scheduler_process.terminate()
            self._scheduler_process.join(timeout=5)
            if self._scheduler_process.is_alive():
                self._scheduler_process.kill()
