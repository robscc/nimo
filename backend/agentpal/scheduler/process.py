"""Scheduler 独立进程入口。

通过 multiprocessing.Process 启动，在独立的 asyncio event loop 中运行
SchedulerBroker，管理 PA / Cron / SubAgent 子进程。
"""

from __future__ import annotations

import asyncio
import multiprocessing
import os
import signal
import sys
from typing import Any


def scheduler_process_main(
    router_addr: str,
    events_addr: str,
    config_dict: dict[str, Any],
    ready_event: multiprocessing.Event | None = None,
) -> None:
    """Scheduler 进程 entry point。

    在新的 asyncio event loop 中：
    1. 设置独立日志
    2. 初始化数据库引擎
    3. 创建 ZMQ context，bind ROUTER/XPUB/XSUB sockets
    4. 创建 SchedulerBroker 并启动
    5. 通知父进程就绪（ready_event.set()）
    6. 等待 SIGTERM 或 SCHEDULER_SHUTDOWN → 优雅关闭

    Args:
        router_addr:  ROUTER socket 绑定地址（ipc://）
        events_addr:  XPUB socket 绑定地址（ipc://）
        config_dict:  已弃用，保留兼容性。子进程从 config.yaml 加载。
        ready_event:  multiprocessing.Event，set() 通知父进程就绪
    """
    _setup_scheduler_logging()

    from loguru import logger

    logger.info(
        f"Scheduler 进程启动: pid={os.getpid()} "
        f"router={router_addr} events={events_addr}"
    )

    try:
        asyncio.run(
            _scheduler_async_main(
                router_addr=router_addr,
                events_addr=events_addr,
                ready_event=ready_event,
            )
        )
    except KeyboardInterrupt:
        logger.info("Scheduler 进程收到 KeyboardInterrupt，退出")
    except Exception as e:
        logger.error(f"Scheduler 进程异常退出: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logger.info(f"Scheduler 进程已退出 (pid={os.getpid()})")


async def _scheduler_async_main(
    router_addr: str,
    events_addr: str,
    ready_event: multiprocessing.Event | None = None,
) -> None:
    """Scheduler 进程异步主函数。"""
    import zmq
    import zmq.asyncio
    from loguru import logger

    from agentpal.scheduler.broker import SchedulerBroker
    from agentpal.scheduler.config import SchedulerConfig

    # 0. 初始化数据库引擎
    await _init_subprocess_db()

    # 1. 构建配置（从 config.yaml 加载最新配置）
    from agentpal.config import get_settings

    settings = get_settings()
    sched_config = SchedulerConfig(
        router_addr=router_addr,
        events_addr=events_addr,
        pa_idle_timeout=settings.scheduler_pa_idle_timeout,
        sub_idle_timeout=settings.scheduler_sub_idle_timeout,
        health_check_interval=settings.scheduler_health_check_interval,
        process_start_timeout=settings.scheduler_process_start_timeout,
    )

    # 2. 计算 XSUB 内部地址
    if events_addr.startswith("ipc://"):
        xsub_addr = events_addr.replace(".sock", "-internal.sock")
    else:
        xsub_addr = events_addr + "-internal"

    # 3. 清理残留 socket 文件
    for addr in (router_addr, events_addr, xsub_addr):
        path = addr.replace("ipc://", "")
        if os.path.exists(path):
            os.unlink(path)
            logger.debug(f"清理残留 socket 文件: {path}")

    # 4. 创建 ZMQ context
    ctx = zmq.asyncio.Context()

    # 5. Bind sockets
    router = ctx.socket(zmq.ROUTER)
    router.setsockopt(zmq.LINGER, 1000)
    router.bind(router_addr)

    xpub = ctx.socket(zmq.XPUB)
    xpub.setsockopt(zmq.LINGER, 1000)
    xpub.bind(events_addr)

    xsub = ctx.socket(zmq.XSUB)
    xsub.setsockopt(zmq.LINGER, 1000)
    xsub.bind(xsub_addr)

    logger.info(
        f"ZMQ sockets 已绑定: router={router_addr} "
        f"xpub={events_addr} xsub={xsub_addr}"
    )

    # 6. 创建并启动 Broker
    mp_ctx = multiprocessing.get_context("spawn")
    broker = SchedulerBroker(
        config=sched_config,
        router_socket=router,
        xpub_socket=xpub,
        xsub_socket=xsub,
        xsub_addr=xsub_addr,
        mp_ctx=mp_ctx,
    )
    await broker.start()

    # 7. 通知父进程就绪
    if ready_event is not None:
        ready_event.set()
    logger.info("Scheduler 进程就绪")

    # 8. 设置 SIGTERM 处理器 — 优雅关闭
    shutdown_event = asyncio.Event()

    def _sigterm_handler(signum: int, frame: Any) -> None:
        logger.info("Scheduler 进程收到 SIGTERM，准备优雅关闭")
        shutdown_event.set()

    signal.signal(signal.SIGTERM, _sigterm_handler)

    # 9. 等待关闭信号
    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Scheduler 进程开始优雅关闭...")
        await broker.stop()

        # 关闭 sockets
        for sock in (router, xpub, xsub):
            sock.close(linger=0)
        ctx.term()

        # 清理 socket 文件
        import contextlib

        for addr in (router_addr, events_addr, xsub_addr):
            path = addr.replace("ipc://", "")
            if os.path.exists(path):
                with contextlib.suppress(OSError):
                    os.unlink(path)

        logger.info("Scheduler 进程已完成清理")


async def _init_subprocess_db() -> None:
    """在子进程中初始化独立的数据库引擎。"""
    from loguru import logger

    try:
        from agentpal.database import init_db

        await init_db()
        logger.info("Scheduler 子进程 DB 引擎初始化完成")
    except Exception as e:
        logger.error(f"Scheduler 子进程 DB 初始化失败: {e}", exc_info=True)
        raise


def _setup_scheduler_logging() -> None:
    """设置 Scheduler 进程独立日志。"""
    from pathlib import Path

    from loguru import logger

    logger.remove()

    # 控制台输出
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
        "<cyan>scheduler</cyan> - <level>{message}</level>",
    )

    # 文件日志
    log_dir = Path.home() / ".nimo" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "scheduler.log"
    logger.add(
        str(log_file),
        level="DEBUG",
        rotation="10 MB",
        retention="3 days",
        encoding="utf-8",
    )
