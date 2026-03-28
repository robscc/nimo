"""Scheduler 配置。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SchedulerConfig:
    """AgentScheduler 运行时配置。

    Attributes:
        router_addr:           ZMQ ROUTER socket 地址（ipc://）
        events_addr:           ZMQ XPUB/XSUB 事件 broker 地址（ipc://）
        pa_idle_timeout:       PA 空闲超时（秒），默认 30 分钟
        sub_idle_timeout:      SubAgent 空闲超时（秒），默认 5 分钟
        health_check_interval: 健康检查间隔（秒）
        process_start_timeout: 子进程启动超时（秒）
        heartbeat_interval:    子进程心跳间隔（秒）
        reaper_interval:       回收检查间隔（秒）
    """

    router_addr: str = "ipc:///tmp/agentpal-router.sock"
    events_addr: str = "ipc:///tmp/agentpal-events.sock"
    pa_idle_timeout: int = 1800  # 30 分钟
    sub_idle_timeout: int = 300  # 5 分钟
    health_check_interval: int = 30
    process_start_timeout: int = 15
    heartbeat_interval: int = 10
    reaper_interval: int = 60
    use_subprocess: bool = True  # False = 旧 in-process daemon 模式（方便测试）
    scheduler_start_timeout: int = 30  # Scheduler 进程启动超时（秒）
    cron_auto_restart: bool = True  # Cron 进程崩溃后是否自动重启
