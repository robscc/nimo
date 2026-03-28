"""单元测试 — SchedulerConfig。"""

from __future__ import annotations

from agentpal.scheduler.config import SchedulerConfig


class TestSchedulerConfig:
    """SchedulerConfig 默认值和覆盖测试。"""

    def test_defaults(self):
        config = SchedulerConfig()
        assert config.router_addr == "ipc:///tmp/agentpal-router.sock"
        assert config.events_addr == "ipc:///tmp/agentpal-events.sock"
        assert config.pa_idle_timeout == 1800
        assert config.sub_idle_timeout == 300
        assert config.health_check_interval == 30
        assert config.process_start_timeout == 15
        assert config.heartbeat_interval == 10
        assert config.reaper_interval == 60

    def test_custom_values(self):
        config = SchedulerConfig(
            router_addr="ipc:///tmp/custom-router.sock",
            events_addr="ipc:///tmp/custom-events.sock",
            pa_idle_timeout=600,
            sub_idle_timeout=120,
            health_check_interval=10,
            process_start_timeout=30,
            heartbeat_interval=5,
            reaper_interval=30,
        )
        assert config.router_addr == "ipc:///tmp/custom-router.sock"
        assert config.events_addr == "ipc:///tmp/custom-events.sock"
        assert config.pa_idle_timeout == 600
        assert config.sub_idle_timeout == 120
        assert config.health_check_interval == 10
        assert config.process_start_timeout == 30
        assert config.heartbeat_interval == 5
        assert config.reaper_interval == 30

    def test_inproc_fallback(self):
        """测试使用 inproc 地址时也能正常创建。"""
        config = SchedulerConfig(
            router_addr="inproc://agent-router",
            events_addr="inproc://agent-events",
        )
        assert config.router_addr == "inproc://agent-router"
        assert config.events_addr == "inproc://agent-events"
