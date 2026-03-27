"""子进程 Worker 入口。

每个 Agent 子进程（PA / SubAgent / Cron）通过此模块启动，
在独立的 asyncio event loop 中运行 Agent 逻辑，
通过 ZMQ ipc:// 与主进程 Scheduler 通信。
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any


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
    3. 创建 ZMQ context（独立于主进程）
    4. 创建 DEALER + PUB socket 连接到主进程 broker
    5. 发送 AGENT_REGISTER 消息
    6. 根据 agent_type 创建对应 Daemon 并进入工作循环
    7. 收到 AGENT_SHUTDOWN 或异常时清理退出

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

    # 1. 创建独立 ZMQ context
    ctx = zmq.asyncio.Context()

    # 2. 创建 DEALER socket 连接 ROUTER
    dealer = ctx.socket(zmq.DEALER)
    dealer.setsockopt(zmq.IDENTITY, identity.encode("utf-8"))
    dealer.setsockopt(zmq.LINGER, 1000)
    dealer.connect(router_addr)

    # 3. 创建 PUB socket 连接 XSUB
    pub = ctx.socket(zmq.PUB)
    pub.setsockopt(zmq.LINGER, 1000)
    pub.connect(events_addr)

    # 短暂等待连接建立
    await asyncio.sleep(0.05)

    # 4. 发送 AGENT_REGISTER
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
    await dealer.send_multipart([b"", register_env.serialize()])
    logger.info(f"Worker {identity} 已发送 AGENT_REGISTER")

    # 5. 根据 agent_type 创建对应 Daemon
    daemon = _create_daemon(
        agent_type=agent_type,
        identity=identity,
        **agent_kwargs,
    )

    # 6. 启动 Daemon（传入独立 ctx）
    await daemon.start(ctx=ctx, router_addr=router_addr, events_addr=events_addr)
    logger.info(f"Worker {identity} daemon 已启动")

    # 7. 等待 daemon 完成（通过 SHUTDOWN 消息或自行结束）
    try:
        while daemon.is_running:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        pass
    finally:
        await daemon.stop()
        dealer.close(linger=0)
        pub.close(linger=0)
        ctx.term()


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

        return SubAgentDaemon(
            agent_name=kwargs.get("agent_name", "default"),
            task_id=kwargs.get("task_id", ""),
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
