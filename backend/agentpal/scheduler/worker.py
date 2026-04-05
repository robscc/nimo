"""子进程 Worker 入口。

每个 Agent 子进程（PA / SubAgent / Cron）通过此模块启动，
在独立的 asyncio event loop 中运行 Agent 逻辑，
通过 ZMQ ipc:// 与主进程 Scheduler 通信。
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from typing import Any

# ── 子进程内的 Scheduler 代理 ──────────────────────────────
# PA 子进程中的工具（如 dispatch_sub_agent）需要通过 Scheduler 派遣 SubAgent，
# 但子进程中没有 FastAPI app.state.scheduler。
# 此模块级变量在 daemon 启动后设置，供 builtin.py._get_scheduler() 使用。
_worker_scheduler_proxy: WorkerSchedulerProxy | None = None


class WorkerSchedulerProxy:
    """PA 子进程中的 Scheduler 轻量代理。

    通过 daemon 的 DEALER socket 向 Scheduler 发送 DISPATCH_SUB 消息，
    实现与 SchedulerClient.dispatch_sub_agent 兼容的接口。
    """

    def __init__(self, daemon: Any) -> None:
        self._daemon = daemon

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
        """向 Scheduler 发送 DISPATCH_SUB 请求。

        Fire-and-forget：不等待 ACK，Scheduler 会异步 spawn 子进程。
        """
        from agentpal.zmq_bus.protocol import Envelope, MessageType

        env = Envelope(
            msg_type=MessageType.DISPATCH_SUB,
            source=self._daemon.identity,
            target="scheduler",
            payload={
                "task_id": task_id,
                "task_prompt": task_prompt,
                "parent_session_id": parent_session_id,
                "agent_name": agent_name,
                "model_config": model_config or {},
                "role_prompt": role_prompt,
                "max_tool_rounds": max_tool_rounds,
            },
        )
        await self._daemon.send_to_router(env)


def worker_main(
    identity: str,
    agent_type: str,
    router_addr: str,
    events_addr: str,
    config_dict: dict[str, Any],
    **agent_kwargs: Any,
) -> None:
    """子进程 entry point。

    在新的 asyncio event loop 中：
    1. 初始化日志（子进程独立日志文件）
    2. 重建 Settings 对象
    3. 初始化数据库引擎（子进程需要独立的 SQLAlchemy engine）
    4. 创建 ZMQ context（独立于主进程）
    5. 创建 DEALER + PUB socket 连接到主进程 broker
    6. 发送 AGENT_REGISTER 消息
    7. 根据 agent_type 创建对应 Daemon 并进入工作循环
    8. 收到 AGENT_SHUTDOWN 或异常时清理退出

    Args:
        identity:     ZMQ socket identity（如 "pa:session-123"）
        agent_type:   Agent 类型 "pa" | "sub_agent" | "cron"
        router_addr:  ROUTER socket 地址（ipc://）
        events_addr:  XPUB broker 地址（ipc://）
        config_dict:  Settings.model_dump() — pickleable
        **agent_kwargs: Agent 特定参数（session_id, task_id 等）
    """
    # 在子进程中设置独立日志
    _setup_worker_logging(identity)

    from loguru import logger

    logger.info(
        f"Worker 启动: identity={identity} agent_type={agent_type} "
        f"pid={os.getpid()}"
    )

    try:
        asyncio.run(
            _worker_async_main(
                identity=identity,
                agent_type=agent_type,
                router_addr=router_addr,
                events_addr=events_addr,
                config_dict=config_dict,
                **agent_kwargs,
            )
        )
    except KeyboardInterrupt:
        logger.info(f"Worker {identity} 收到 KeyboardInterrupt，退出")
    except Exception as e:
        logger.error(f"Worker {identity} 异常退出: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info(f"Worker {identity} 已退出 (pid={os.getpid()})")


async def _worker_async_main(
    identity: str,
    agent_type: str,
    router_addr: str,
    events_addr: str,
    config_dict: dict[str, Any],
    **agent_kwargs: Any,
) -> None:
    """子进程异步主函数。"""
    import zmq
    import zmq.asyncio
    from loguru import logger

    from agentpal.zmq_bus.protocol import Envelope, MessageType

    # 0. 初始化数据库引擎（子进程需要独立的 SQLAlchemy engine）
    await _init_subprocess_db()

    # 1. 创建独立 ZMQ context
    ctx = zmq.asyncio.Context()

    # 2. 根据 agent_type 创建对应 Daemon
    daemon = _create_daemon(
        agent_type=agent_type,
        identity=identity,
        **agent_kwargs,
    )

    # 3. 设置 SIGTERM 处理器 — 优雅停止
    _shutdown_event = asyncio.Event()

    def _sigterm_handler(signum: int, frame: Any) -> None:
        logger.info(f"Worker {identity} 收到 SIGTERM，准备优雅关闭")
        _shutdown_event.set()

    signal.signal(signal.SIGTERM, _sigterm_handler)

    # 4. 启动 Daemon — 创建 DEALER + PUB socket 并启动 recv/work loop
    await daemon.start(ctx=ctx, router_addr=router_addr, events_addr=events_addr)
    logger.info(f"Worker {identity} daemon 已启动")

    # 短暂等待连接建立
    await asyncio.sleep(0.05)

    # 5. 通过 daemon 的 DEALER socket 发送 AGENT_REGISTER
    # 关键：必须在 daemon.start() 之后发送，这样 broker 收到 REGISTER 后
    # 立即发送的 DISPATCH_TASK 能被 daemon 的 _recv_loop 接收。
    # 如果用单独的注册 DEALER 发送，broker 回复时该 socket 已关闭，消息丢失。
    register_env = Envelope(
        msg_type=MessageType.AGENT_REGISTER,
        source=identity,
        target="scheduler",
        payload={
            "agent_type": agent_type,
            "pid": os.getpid(),
            **{k: v for k, v in agent_kwargs.items() if isinstance(v, (str, int, float, bool, type(None)))},
        },
    )
    await daemon.send_to_router(register_env)
    logger.info(f"Worker {identity} 已发送 AGENT_REGISTER")

    # 6. PA 子进程：注册 WorkerSchedulerProxy，
    # 使 builtin.py 的 dispatch_sub_agent 工具能通过 Scheduler 派遣 SubAgent
    global _worker_scheduler_proxy
    if agent_type == "pa":
        _worker_scheduler_proxy = WorkerSchedulerProxy(daemon)
        logger.info(f"Worker {identity} 已注册 WorkerSchedulerProxy")

    # 7. 启动心跳后台任务（使用 daemon 的 send_to_router）
    heartbeat_task = asyncio.create_task(
        _heartbeat_loop(daemon, identity),
        name=f"heartbeat-{identity}",
    )

    # 8. 等待 daemon 完成（通过 SHUTDOWN 消息或自行结束）或 SIGTERM
    try:
        while daemon.is_running and not _shutdown_event.is_set():
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        # 停止心跳
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

        await daemon.stop()

        # daemon.stop() 已关闭 daemon 的 DEALER/PUB socket，
        # 这里只需终止 ZMQ context。
        ctx.term()


async def _heartbeat_loop(
    daemon: Any,
    identity: str,
    interval: float = 10.0,
) -> None:
    """定时向 Scheduler 发送心跳，更新活跃时间。

    使用 daemon 的 send_to_router 方法发送，
    因为 worker 的注册用 DEALER 在 daemon.start() 前已关闭。
    """
    from loguru import logger

    from agentpal.zmq_bus.protocol import Envelope, MessageType

    while True:
        try:
            await asyncio.sleep(interval)
            heartbeat = Envelope(
                msg_type=MessageType.AGENT_HEARTBEAT,
                source=identity,
                target="scheduler",
            )
            await daemon.send_to_router(heartbeat)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Worker {identity} 心跳发送失败: {e}")


async def _init_subprocess_db() -> None:
    """在子进程中初始化独立的数据库引擎。

    spawn 模式下子进程不继承主进程的 SQLAlchemy engine，
    需要重新 import 触发模块级别的 engine 创建。

    注意：不调用 init_db()（create_all + WAL 验证），因为：
    1. 表已由主进程（FastAPI lifespan）创建，无需重复
    2. create_all 需要 SQLite 写锁，当其他进程持有锁时会阻塞数秒，
       导致 AGENT_REGISTER 延迟发送，broker 的 _wait_for_register 超时，
       PA 子进程启动卡住
    3. WAL 模式通过 _set_sqlite_pragma event listener 在每个连接上自动设置
    """
    from loguru import logger

    try:
        # 仅 import 触发模块级 engine/session 工厂创建，不做 DDL
        import agentpal.database  # noqa: F401

        logger.info("子进程 DB 引擎初始化完成（跳过 create_all）")
    except Exception as e:
        logger.error(f"子进程 DB 初始化失败: {e}", exc_info=True)
        raise


def _create_daemon(
    agent_type: str,
    identity: str,
    **kwargs: Any,
) -> Any:
    """根据 agent_type 创建对应的 Daemon 实例。"""
    if agent_type == "pa":
        from agentpal.zmq_bus.pa_daemon import PersonalAssistantDaemon

        session_id = kwargs.get("session_id", "")
        return PersonalAssistantDaemon(session_id=session_id)

    elif agent_type == "sub_agent":
        from agentpal.zmq_bus.sub_daemon import SubAgentDaemon
        from agentpal.config import get_settings

        settings = get_settings()
        return SubAgentDaemon(
            agent_name=kwargs.get("agent_name", "default"),
            task_id=kwargs.get("task_id", ""),
            model_config=kwargs.get("model_config"),
            role_prompt=kwargs.get("role_prompt", ""),
            max_tool_rounds=kwargs.get("max_tool_rounds", settings.sub_agent_max_tool_rounds),
            parent_session_id=kwargs.get("parent_session_id", ""),
        )

    elif agent_type == "cron":
        from agentpal.zmq_bus.cron_daemon import CronDaemon

        return CronDaemon()

    else:
        raise ValueError(f"Unknown agent_type: {agent_type}")


def _setup_worker_logging(identity: str) -> None:
    """设置子进程独立日志。

    日志写到 ~/.nimo/logs/worker-{identity}.log
    """
    from pathlib import Path

    from loguru import logger

    # 清除默认 handler
    logger.remove()

    # 控制台输出
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    )

    # 文件日志
    log_dir = Path.home() / ".nimo" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    safe_identity = identity.replace(":", "_").replace("/", "_")
    log_file = log_dir / f"worker-{safe_identity}.log"
    logger.add(
        str(log_file),
        level="DEBUG",
        rotation="10 MB",
        retention="3 days",
        encoding="utf-8",
    )
