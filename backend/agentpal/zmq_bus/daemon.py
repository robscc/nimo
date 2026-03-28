"""AgentDaemon — Agent 守护进程基类。

每个 Agent（PA / SubAgent / Cron）对应一个 daemon 实例，
通过 DEALER socket 收发消息，PUB socket 发布事件流。

设计要点：
- FIFO 消息处理（asyncio.Queue → 单线程逐条处理）
- 每个 task 创建独立 DB session，处理完立即释放
- 优雅关闭（drain queue → close sockets）
- last_active_at 追踪空闲时间，供 manager 回收
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import zmq
import zmq.asyncio
from loguru import logger

from agentpal.zmq_bus.protocol import Envelope, MessageType


class AgentDaemon:
    """Agent 守护进程基类。

    子类需实现 :meth:`handle_message` 处理具体消息逻辑。

    Attributes:
        identity:       ZMQ socket identity（如 "pa:session-123"）
        last_active_at: 最近一次处理消息的时间戳
    """

    def __init__(self, identity: str) -> None:
        self.identity = identity
        self.last_active_at: float = time.time()

        self._dealer: zmq.asyncio.Socket | None = None
        self._pub: zmq.asyncio.Socket | None = None
        self._task_queue: asyncio.Queue[Envelope] = asyncio.Queue(maxsize=256)
        self._running = False
        self._recv_task: asyncio.Task | None = None
        self._work_task: asyncio.Task | None = None
        self._ctx: zmq.asyncio.Context | None = None
        self._own_ctx: bool = False  # 是否由 daemon 自己创建的 ctx

    # ── 生命周期 ──────────────────────────────────────────

    async def start(
        self,
        ctx: zmq.asyncio.Context | None = None,
        router_addr: str = "",
        events_addr: str = "",
    ) -> None:
        """连接 DEALER/PUB socket，启动 recv_loop + work_loop。

        Args:
            ctx:          共享的 zmq.asyncio.Context（None 时自动创建独立 ctx）
            router_addr:  ROUTER socket 地址（如 "inproc://agent-router"）
            events_addr:  XPUB broker 地址（如 "inproc://agent-events"）
        """
        if ctx is None:
            self._own_ctx = True
            ctx = zmq.asyncio.Context()
        else:
            self._own_ctx = False
        self._ctx = ctx
        self._running = True

        # DEALER socket — 收发消息
        self._dealer = ctx.socket(zmq.DEALER)
        self._dealer.setsockopt(zmq.IDENTITY, self.identity.encode("utf-8"))
        self._dealer.setsockopt(zmq.LINGER, 1000)  # 关闭时最多等 1 秒发完残留消息
        self._dealer.connect(router_addr)

        # PUB socket — 发布事件流
        self._pub = ctx.socket(zmq.PUB)
        self._pub.setsockopt(zmq.LINGER, 1000)
        self._pub.connect(events_addr)

        # 启动接收 / 处理循环
        self._recv_task = asyncio.create_task(
            self._recv_loop(), name=f"daemon-recv-{self.identity}"
        )
        self._work_task = asyncio.create_task(
            self._work_loop(), name=f"daemon-work-{self.identity}"
        )

        logger.info(f"AgentDaemon [{self.identity}] 已启动")

    async def stop(self) -> None:
        """优雅关闭：停止循环 → 等待队列 drain → 关闭 socket。"""
        self._running = False

        # 取消接收循环
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass

        # 等待工作循环处理完当前消息（最多 5 秒）
        if self._work_task and not self._work_task.done():
            try:
                await asyncio.wait_for(self._work_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._work_task.cancel()
                try:
                    await self._work_task
                except asyncio.CancelledError:
                    pass

        # 关闭 socket
        if self._dealer is not None:
            self._dealer.close(linger=0)
            self._dealer = None
        if self._pub is not None:
            self._pub.close(linger=0)
            self._pub = None

        # 如果是自己创建的 ctx，负责终止
        if self._own_ctx and self._ctx is not None:
            self._ctx.term()
            self._ctx = None

        logger.info(f"AgentDaemon [{self.identity}] 已停止")

    @property
    def is_running(self) -> bool:
        return self._running

    # ── 消息处理（子类实现）────────────────────────────────

    async def handle_message(self, envelope: Envelope) -> None:
        """处理一条消息。子类必须实现。

        Args:
            envelope: 接收到的消息信封
        """
        raise NotImplementedError(
            f"{type(self).__name__} must implement handle_message()"
        )

    # ── 发布事件 ──────────────────────────────────────────

    async def publish_event(self, topic: str, envelope: Envelope) -> None:
        """通过 PUB socket 发布事件。

        Args:
            topic:    事件主题（如 "session:abc123"）
            envelope: 事件信封
        """
        if self._pub is None:
            logger.warning(f"AgentDaemon [{self.identity}] PUB socket 未就绪，丢弃事件")
            return
        try:
            await self._pub.send_multipart([
                topic.encode("utf-8"),
                envelope.serialize(),
            ])
        except zmq.ZMQError as e:
            logger.error(f"AgentDaemon [{self.identity}] 发布事件失败: {e}")

    async def send_to_router(self, envelope: Envelope) -> None:
        """通过 DEALER socket 向 ROUTER 发送消息（路由到其他 daemon）。

        DEALER 帧格式：[b"", envelope_bytes]
        ROUTER 会自动在前面加上 identity 帧。

        Args:
            envelope: 要发送的消息信封
        """
        if self._dealer is None:
            logger.warning(f"AgentDaemon [{self.identity}] DEALER socket 未就绪，丢弃消息")
            return
        try:
            await self._dealer.send_multipart([
                b"",
                envelope.serialize(),
            ])
        except zmq.ZMQError as e:
            logger.error(f"AgentDaemon [{self.identity}] 发送消息失败: {e}")

    # ── 内部循环 ──────────────────────────────────────────

    async def _recv_loop(self) -> None:
        """DEALER socket 接收循环：解析 Envelope 放入 FIFO 队列。"""
        assert self._dealer is not None

        while self._running:
            try:
                # DEALER 收到: [b"", envelope_bytes]
                frames = await self._dealer.recv_multipart()
                if len(frames) < 2:
                    logger.warning(
                        f"AgentDaemon [{self.identity}] 收到格式异常帧: {len(frames)} frames"
                    )
                    continue

                envelope = Envelope.deserialize(frames[-1])
                await self._task_queue.put(envelope)

            except zmq.ZMQError as e:
                if e.errno == zmq.ETERM:
                    break  # Context 已终止
                if self._running:
                    logger.error(f"AgentDaemon [{self.identity}] recv 异常: {e}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.error(f"AgentDaemon [{self.identity}] recv_loop 异常: {e}")

    async def _work_loop(self) -> None:
        """FIFO 工作循环：逐条取出消息并处理。"""
        while self._running or not self._task_queue.empty():
            try:
                # 使用超时以便检查 _running 状态
                try:
                    envelope = await asyncio.wait_for(
                        self._task_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                self.last_active_at = time.time()

                # 拦截 CONFIG_RELOAD 消息 — 在基类层统一处理
                if envelope.msg_type == MessageType.CONFIG_RELOAD:
                    self._handle_config_reload()
                    self._task_queue.task_done()
                    continue

                try:
                    await self.handle_message(envelope)
                except Exception as e:
                    logger.error(
                        f"AgentDaemon [{self.identity}] 处理消息异常: "
                        f"msg_type={envelope.msg_type} error={e}",
                        exc_info=True,
                    )
                finally:
                    self._task_queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._running:
                    logger.error(f"AgentDaemon [{self.identity}] work_loop 异常: {e}")

    def _handle_config_reload(self) -> None:
        """处理 CONFIG_RELOAD 消息：清除 Settings LRU 缓存。"""
        try:
            from agentpal.config import get_settings

            get_settings.cache_clear()
            logger.info(f"AgentDaemon [{self.identity}] 已刷新 Settings 缓存 (CONFIG_RELOAD)")
        except Exception as e:
            logger.warning(f"AgentDaemon [{self.identity}] CONFIG_RELOAD 处理失败: {e}")
