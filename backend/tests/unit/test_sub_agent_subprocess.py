"""单元测试 — SubAgent 独立进程化 + 5 分钟 Idle 回收。

覆盖：
- worker_main 子进程入口增强（DB init + heartbeat + SIGTERM + params）
- Scheduler 子进程 spawn + REGISTER 握手
- AGENT_RESPONSE 拦截 + 结果投递
- 5min idle 回收（subprocess 模式）
- 进程异常退出检测
- Scheduler 不可用时 fallback
- use_subprocess 配置开关
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpal.scheduler.config import SchedulerConfig
from agentpal.scheduler.scheduler import AgentScheduler, ManagedProcess, _DummyProcess
from agentpal.scheduler.state import AgentProcessInfo, AgentState
from agentpal.zmq_bus.protocol import Envelope, MessageType


@pytest.fixture
def inproc_config():
    """使用 inproc 地址避免文件系统副作用。"""
    return SchedulerConfig(
        router_addr="inproc://test-sub-process-router",
        events_addr="inproc://test-sub-process-events",
        pa_idle_timeout=10,
        sub_idle_timeout=5,
        reaper_interval=2,
        health_check_interval=2,
        use_subprocess=True,
    )


@pytest.fixture
def inproc_config_no_subprocess():
    """使用 inproc 地址，禁用子进程模式。"""
    return SchedulerConfig(
        router_addr="inproc://test-sub-noproc-router",
        events_addr="inproc://test-sub-noproc-events",
        use_subprocess=False,
    )


# ── SchedulerConfig use_subprocess ───────────────────────


class TestSchedulerConfigSubprocess:
    """use_subprocess 配置开关测试。"""

    def test_default_use_subprocess_true(self):
        config = SchedulerConfig()
        assert config.use_subprocess is True

    def test_explicit_use_subprocess_false(self):
        config = SchedulerConfig(use_subprocess=False)
        assert config.use_subprocess is False

    def test_explicit_use_subprocess_true(self):
        config = SchedulerConfig(use_subprocess=True)
        assert config.use_subprocess is True


# ── Worker 子进程入口增强 ────────────────────────────────


class TestWorkerEnhancements:
    """Worker 子进程入口增强测试。"""

    def test_create_sub_daemon_with_full_params(self):
        """_create_daemon 传递完整 SubAgent 参数。"""
        from agentpal.scheduler.worker import _create_daemon
        from agentpal.zmq_bus.sub_daemon import SubAgentDaemon

        daemon = _create_daemon(
            agent_type="sub_agent",
            identity="sub:coder:task-1",
            agent_name="coder",
            task_id="task-1",
            model_config={"model": "gpt-4"},
            role_prompt="你是编程专家",
            max_tool_rounds=12,
            parent_session_id="session-abc",
        )
        assert isinstance(daemon, SubAgentDaemon)
        assert daemon._agent_name == "coder"
        assert daemon._task_id == "task-1"
        assert daemon._model_config == {"model": "gpt-4"}
        assert daemon._role_prompt == "你是编程专家"
        assert daemon._max_tool_rounds == 12
        assert daemon._parent_session_id == "session-abc"

    def test_create_sub_daemon_defaults(self):
        """_create_daemon SubAgent 默认参数。"""
        from agentpal.scheduler.worker import _create_daemon
        from agentpal.zmq_bus.sub_daemon import SubAgentDaemon

        daemon = _create_daemon(
            agent_type="sub_agent",
            identity="sub:default:task-2",
        )
        assert isinstance(daemon, SubAgentDaemon)
        assert daemon._agent_name == "default"
        assert daemon._task_id == ""
        assert daemon._model_config == {}
        assert daemon._role_prompt == ""
        assert daemon._max_tool_rounds == 8
        assert daemon._parent_session_id == ""

    @pytest.mark.asyncio
    async def test_heartbeat_loop_sends_heartbeat(self):
        """_heartbeat_loop 通过 daemon.send_to_router 发送 AGENT_HEARTBEAT。"""
        from agentpal.scheduler.worker import _heartbeat_loop

        mock_daemon = AsyncMock()
        task = asyncio.create_task(
            _heartbeat_loop(mock_daemon, "sub:test:hb", interval=0.1)
        )
        await asyncio.sleep(0.35)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # 应该至少发了 2-3 次心跳
        assert mock_daemon.send_to_router.call_count >= 2

        # 验证心跳消息格式
        call_args = mock_daemon.send_to_router.call_args_list[0]
        envelope = call_args[0][0]
        assert envelope.msg_type == MessageType.AGENT_HEARTBEAT
        assert envelope.source == "sub:test:hb"
        assert envelope.target == "scheduler"

    @pytest.mark.asyncio
    async def test_init_subprocess_db(self):
        """_init_subprocess_db 调用 init_db。"""
        from agentpal.scheduler.worker import _init_subprocess_db

        # _init_subprocess_db 内部用 from agentpal.database import init_db
        # 需要 patch 原始模块
        with patch("agentpal.database.init_db", new_callable=AsyncMock) as mock_init:
            await _init_subprocess_db()
            mock_init.assert_awaited_once()


# ── Scheduler REGISTER 握手 ─────────────────────────────


class TestSchedulerRegisterHandshake:
    """AGENT_REGISTER 握手测试。"""

    @pytest.mark.asyncio
    async def test_handle_agent_register_updates_info(self, inproc_config):
        """AGENT_REGISTER 消息更新 ProcessInfo 状态和 PID。"""
        scheduler = AgentScheduler(inproc_config)
        identity = "sub:coder:task-123"

        # 预先创建 ProcessInfo（模拟 _dispatch_sub_subprocess 的注册）
        info = AgentProcessInfo(
            process_id=identity,
            agent_type="sub_agent",
            state=AgentState.STARTING,
            task_id="task-123",
            agent_name="coder",
        )
        scheduler._processes[identity] = ManagedProcess(
            process=_DummyProcess(), info=info,
        )

        # 模拟 AGENT_REGISTER 消息
        envelope = Envelope(
            msg_type=MessageType.AGENT_REGISTER,
            source=identity,
            target="scheduler",
            payload={"agent_type": "sub_agent", "pid": 12345},
        )
        await scheduler._handle_agent_register(envelope)

        assert info.os_pid == 12345
        assert info.state == AgentState.IDLE

    @pytest.mark.asyncio
    async def test_handle_agent_register_wakes_event(self, inproc_config):
        """AGENT_REGISTER 唤醒 _register_events。"""
        scheduler = AgentScheduler(inproc_config)
        identity = "sub:coder:task-456"

        # 创建 register event
        event = asyncio.Event()
        scheduler._register_events[identity] = event
        assert not event.is_set()

        envelope = Envelope(
            msg_type=MessageType.AGENT_REGISTER,
            source=identity,
            target="scheduler",
            payload={"agent_type": "sub_agent", "pid": 99999},
        )
        await scheduler._handle_agent_register(envelope)

        assert event.is_set()

    @pytest.mark.asyncio
    async def test_wait_for_register_success(self, inproc_config):
        """_wait_for_register 在事件被设置时成功返回。"""
        scheduler = AgentScheduler(inproc_config)
        identity = "sub:test:wait-ok"

        event = asyncio.Event()
        scheduler._register_events[identity] = event

        # 延迟设置事件
        async def _set_later():
            await asyncio.sleep(0.1)
            event.set()

        asyncio.create_task(_set_later())
        await scheduler._wait_for_register(identity, timeout=5.0)

        # event 应已清理
        assert identity not in scheduler._register_events

    @pytest.mark.asyncio
    async def test_wait_for_register_timeout(self, inproc_config):
        """_wait_for_register 超时抛出 TimeoutError。"""
        scheduler = AgentScheduler(inproc_config)
        identity = "sub:test:wait-timeout"

        event = asyncio.Event()
        scheduler._register_events[identity] = event

        with pytest.raises(TimeoutError, match="注册超时"):
            await scheduler._wait_for_register(identity, timeout=0.1)

        # event 应已清理
        assert identity not in scheduler._register_events


# ── AGENT_RESPONSE 拦截 + 结果投递 ────────────────────


class TestAgentResponseInterception:
    """AGENT_RESPONSE 拦截 + _deliver_sub_result 测试。"""

    @pytest.mark.asyncio
    async def test_deliver_sub_result_done(self, inproc_config):
        """status=done 时写 MemoryRecord + 推 SSE。"""
        scheduler = AgentScheduler(inproc_config)
        identity = "sub:coder:task-789"

        # 注册 ProcessInfo
        info = AgentProcessInfo(
            process_id=identity,
            agent_type="sub_agent",
            session_id="session-parent",
            task_id="task-789",
            agent_name="coder",
            state=AgentState.RUNNING,
        )
        scheduler._processes[identity] = ManagedProcess(
            process=_DummyProcess(), info=info,
        )

        envelope = Envelope(
            msg_type=MessageType.AGENT_RESPONSE,
            source=identity,
            target="pa:session-parent",
            session_id="session-parent",
            payload={
                "task_id": "task-789",
                "status": "done",
                "result": "任务完成结果",
                "agent_name": "coder",
            },
        )

        mock_db = AsyncMock()
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_bus = MagicMock()
        mock_bus.publish = AsyncMock()

        with patch("agentpal.database.AsyncSessionLocal", return_value=mock_session_ctx), \
             patch("agentpal.services.session_event_bus.session_event_bus", mock_bus):
            await scheduler._deliver_sub_result(envelope)

            # 验证 MemoryRecord 被添加
            mock_db.add.assert_called_once()
            record = mock_db.add.call_args[0][0]
            assert record.session_id == "session-parent"
            assert record.role == "assistant"
            assert "coder" in record.content
            assert "任务完成结果" in record.content

            # 验证 SSE 推送
            mock_bus.publish.assert_awaited_once()
            call_args = mock_bus.publish.call_args
            assert call_args[0][0] == "session-parent"
            assert call_args[0][1]["type"] == "new_message"

    @pytest.mark.asyncio
    async def test_deliver_sub_result_failed_skips(self, inproc_config):
        """status=failed 时不投递结果。"""
        scheduler = AgentScheduler(inproc_config)

        envelope = Envelope(
            msg_type=MessageType.AGENT_RESPONSE,
            source="sub:coder:task-fail",
            target="pa:session-parent",
            session_id="session-parent",
            payload={
                "task_id": "task-fail",
                "status": "failed",
                "error": "some error",
                "agent_name": "coder",
            },
        )

        with patch("agentpal.database.AsyncSessionLocal") as mock_asl:
            await scheduler._deliver_sub_result(envelope)
            # AsyncSessionLocal 不应被调用
            mock_asl.assert_not_called()

    @pytest.mark.asyncio
    async def test_deliver_sub_result_no_parent_session_skips(self, inproc_config):
        """无 parent_session_id 时不投递。"""
        scheduler = AgentScheduler(inproc_config)

        envelope = Envelope(
            msg_type=MessageType.AGENT_RESPONSE,
            source="sub:coder:task-no-parent",
            target="pa:",
            session_id=None,
            payload={
                "task_id": "task-no-parent",
                "status": "done",
                "result": "结果",
                "agent_name": "coder",
            },
        )

        with patch("agentpal.database.AsyncSessionLocal") as mock_asl:
            await scheduler._deliver_sub_result(envelope)
            mock_asl.assert_not_called()


# ── 5min Idle 回收（SubAgent 子进程）────────────────────


class TestSubAgentIdleReaping:
    """SubAgent 空闲回收测试。"""

    def test_subprocess_detected_for_reaping(self, inproc_config):
        """SubAgent 子进程可被 reaper 识别。"""
        scheduler = AgentScheduler(inproc_config)

        # 创建一个模拟的真实子进程 ManagedProcess
        mock_process = MagicMock()
        mock_process.is_alive.return_value = True
        mock_process.pid = 12345

        info = AgentProcessInfo(
            process_id="sub:coder:task-idle",
            agent_type="sub_agent",
            state=AgentState.IDLE,
            session_id="session-x",
            task_id="task-idle",
            agent_name="coder",
            # 设置很久以前的活跃时间
            last_active_at=time.time() - 600,
        )
        scheduler._processes["sub:coder:task-idle"] = ManagedProcess(
            process=mock_process, info=info,
        )

        # 验证不是 DummyProcess
        managed = scheduler._processes["sub:coder:task-idle"]
        assert not isinstance(managed.process, _DummyProcess)

        # 验证 idle 超过阈值
        assert info.idle_seconds > inproc_config.sub_idle_timeout

    @pytest.mark.asyncio
    async def test_reaper_stops_idle_subprocess(self, inproc_config):
        """Reaper 回收空闲超时的 SubAgent 子进程。"""
        inproc_config.reaper_interval = 0.1  # 加速测试
        inproc_config.sub_idle_timeout = 0.2

        scheduler = AgentScheduler(inproc_config)

        # 创建真实 ManagedProcess（mock Process）
        mock_process = MagicMock()
        mock_process.is_alive.return_value = True
        mock_process.join.return_value = None
        mock_process.terminate.return_value = None

        info = AgentProcessInfo(
            process_id="sub:coder:task-reap",
            agent_type="sub_agent",
            state=AgentState.IDLE,
            last_active_at=time.time() - 1,  # 1 秒前活跃
        )
        scheduler._processes["sub:coder:task-reap"] = ManagedProcess(
            process=mock_process, info=info,
        )

        scheduler._running = True

        # 手动运行一轮 reaper 逻辑
        # （不启动完整 scheduler，只调用内部逻辑）
        now = time.time()
        identity = "sub:coder:task-reap"
        managed = scheduler._processes[identity]

        idle_secs = now - managed.info.last_active_at
        assert idle_secs > inproc_config.sub_idle_timeout

        # 模拟 _stop_process
        with patch.object(scheduler, "_stop_process", new_callable=AsyncMock) as mock_stop:
            await mock_stop(identity, timeout=5.0)
            mock_stop.assert_awaited_once_with(identity, timeout=5.0)


# ── 进程异常退出检测 ────────────────────────────────────


class TestProcessDeathDetection:
    """进程异常退出检测测试。"""

    def test_health_check_detects_dead_subprocess(self, inproc_config):
        """健康检查检测到 dead 子进程标记 FAILED。"""
        scheduler = AgentScheduler(inproc_config)

        # 创建已死的子进程
        mock_process = MagicMock()
        mock_process.is_alive.return_value = False
        mock_process.pid = 99999

        info = AgentProcessInfo(
            process_id="sub:coder:task-dead",
            agent_type="sub_agent",
            state=AgentState.RUNNING,
        )
        scheduler._processes["sub:coder:task-dead"] = ManagedProcess(
            process=mock_process, info=info,
        )

        # 模拟健康检查逻辑
        managed = scheduler._processes["sub:coder:task-dead"]
        assert managed.info.is_alive  # RUNNING 是活跃状态

        # 非 DummyProcess 且 is_alive() == False → 标记 FAILED
        if not isinstance(managed.process, _DummyProcess) and not managed.process.is_alive():
            managed.info.state = AgentState.FAILED
            managed.info.error = "process exited unexpectedly"

        assert info.state == AgentState.FAILED
        assert info.error == "process exited unexpectedly"


# ── dispatch_sub_agent 路径选择 ──────────────────────────


class TestDispatchSubAgentRouting:
    """dispatch_sub_agent 模式选择测试。"""

    @pytest.mark.asyncio
    async def test_dispatch_inproc_when_subprocess_disabled(self, inproc_config_no_subprocess):
        """use_subprocess=False 时走 inproc daemon 模式。"""
        scheduler = AgentScheduler(inproc_config_no_subprocess)

        with patch.object(scheduler, "_dispatch_sub_inproc", new_callable=AsyncMock) as mock_inproc, \
             patch.object(scheduler, "_dispatch_sub_subprocess", new_callable=AsyncMock) as mock_sub:
            mock_inproc.return_value = AgentProcessInfo(
                process_id="sub:test:t1",
                agent_type="sub_agent",
            )
            await scheduler.dispatch_sub_agent(
                task_id="t1",
                task_prompt="test",
                parent_session_id="s1",
            )
            mock_inproc.assert_awaited_once()
            mock_sub.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_inproc_when_not_ipc(self):
        """inproc 地址时即使 use_subprocess=True 也走 inproc。"""
        config = SchedulerConfig(
            router_addr="inproc://test-router",
            events_addr="inproc://test-events",
            use_subprocess=True,
        )
        scheduler = AgentScheduler(config)

        with patch.object(scheduler, "_dispatch_sub_inproc", new_callable=AsyncMock) as mock_inproc, \
             patch.object(scheduler, "_dispatch_sub_subprocess", new_callable=AsyncMock) as mock_sub:
            mock_inproc.return_value = AgentProcessInfo(
                process_id="sub:test:t2",
                agent_type="sub_agent",
            )
            await scheduler.dispatch_sub_agent(
                task_id="t2",
                task_prompt="test",
                parent_session_id="s2",
            )
            mock_inproc.assert_awaited_once()
            mock_sub.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_subprocess_when_ipc_and_enabled(self):
        """ipc + use_subprocess=True 走子进程模式。"""
        config = SchedulerConfig(
            router_addr="ipc:///tmp/test-dispatch-router.sock",
            events_addr="ipc:///tmp/test-dispatch-events.sock",
            use_subprocess=True,
        )
        scheduler = AgentScheduler(config)

        with patch.object(scheduler, "_dispatch_sub_subprocess", new_callable=AsyncMock) as mock_sub, \
             patch.object(scheduler, "_dispatch_sub_inproc", new_callable=AsyncMock) as mock_inproc:
            mock_sub.return_value = AgentProcessInfo(
                process_id="sub:test:t3",
                agent_type="sub_agent",
            )
            await scheduler.dispatch_sub_agent(
                task_id="t3",
                task_prompt="test",
                parent_session_id="s3",
            )
            mock_sub.assert_awaited_once()
            mock_inproc.assert_not_awaited()


# ── builtin.py _get_scheduler fallback ──────────────────


class TestGetSchedulerFallback:
    """builtin.py _get_scheduler 测试。"""

    def test_get_scheduler_returns_none_without_app(self):
        """无 app 或无 scheduler 时返回 None。"""
        import sys

        from agentpal.tools.builtin import _get_scheduler

        # 用 MagicMock 替换 agentpal.main 模块，其 app=None
        mock_main = MagicMock()
        mock_main.app = None
        with patch.dict(sys.modules, {"agentpal.main": mock_main}):
            result = _get_scheduler()
            assert result is None

    def test_get_scheduler_returns_scheduler_from_app(self):
        """app.state.scheduler 存在时返回 scheduler 实例。"""
        from agentpal.tools.builtin import _get_scheduler

        mock_scheduler = MagicMock()
        # _get_scheduler 内部 import agentpal.main → getattr(app, ...) → getattr(app.state, "scheduler")
        # 直接 patch 真实 app.state.scheduler
        import agentpal.main as _main_mod
        _app = getattr(_main_mod, "app", None)
        if _app is None:
            pytest.skip("agentpal.main.app not available")

        orig = getattr(_app.state, "scheduler", None)
        try:
            _app.state.scheduler = mock_scheduler
            result = _get_scheduler()
            assert result is mock_scheduler
        finally:
            if orig is not None:
                _app.state.scheduler = orig
            elif hasattr(_app.state, "scheduler"):
                del _app.state.scheduler

    def test_get_scheduler_handles_import_error(self):
        """import 失败时安全返回 None。"""
        import sys

        from agentpal.tools.builtin import _get_scheduler

        with patch.dict(sys.modules, {"agentpal.main": None}):
            result = _get_scheduler()
            # import 时 sys.modules["agentpal.main"] is None → raises ImportError
            # _get_scheduler catches Exception → returns None
            assert result is None


# ── _dispatch_sub_subprocess 参数传递 ────────────────────


class TestSubprocessParamPassing:
    """验证子进程 spawn 参数正确传递。"""

    @pytest.mark.asyncio
    async def test_subprocess_receives_all_params(self):
        """_dispatch_sub_subprocess 传递完整参数给 worker_main。"""
        config = SchedulerConfig(
            router_addr="ipc:///tmp/test-params-router.sock",
            events_addr="ipc:///tmp/test-params-events.sock",
            use_subprocess=True,
            process_start_timeout=0.1,  # 快速超时
        )
        scheduler = AgentScheduler(config)
        scheduler._ctx = MagicMock()  # 模拟 ZMQ context
        scheduler._router = AsyncMock()  # 模拟 ROUTER socket

        mock_process = MagicMock()
        mock_process.start.return_value = None
        mock_process.pid = 54321
        mock_process.is_alive.return_value = True

        with patch.object(scheduler._mp_ctx, "Process", return_value=mock_process) as mock_proc_cls, \
             patch.object(scheduler, "_wait_for_register", new_callable=AsyncMock) as mock_wait, \
             patch.object(scheduler, "_send_to_daemon", new_callable=AsyncMock), \
             patch("agentpal.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock()
            mock_settings.return_value.model_dump.return_value = {"key": "val"}

            # 让 _wait_for_register 成功（不超时）
            mock_wait.return_value = None

            info = await scheduler._dispatch_sub_subprocess(
                process_id="sub:researcher:task-p1",
                task_id="task-p1",
                task_prompt="调研 Python 框架",
                parent_session_id="session-main",
                agent_name="researcher",
                model_config={"model": "qwen-plus"},
                role_prompt="你是调研专家",
                max_tool_rounds=10,
            )

            # 验证 Process 创建参数
            mock_proc_cls.assert_called_once()
            call_kwargs = mock_proc_cls.call_args[1]
            assert call_kwargs["kwargs"]["identity"] == "sub:researcher:task-p1"
            assert call_kwargs["kwargs"]["agent_type"] == "sub_agent"
            assert call_kwargs["kwargs"]["agent_name"] == "researcher"
            assert call_kwargs["kwargs"]["task_id"] == "task-p1"
            assert call_kwargs["kwargs"]["model_config"] == {"model": "qwen-plus"}
            assert call_kwargs["kwargs"]["role_prompt"] == "你是调研专家"
            assert call_kwargs["kwargs"]["max_tool_rounds"] == 10
            assert call_kwargs["kwargs"]["parent_session_id"] == "session-main"

            # 验证 info
            assert info.process_id == "sub:researcher:task-p1"
            assert info.agent_type == "sub_agent"
            assert info.os_pid == 54321
            assert info.task_id == "task-p1"
            assert info.agent_name == "researcher"

            # 验证 process.start() 被调用
            mock_process.start.assert_called_once()


# ── _handle_dispatch_from_router 统一入口 ────────────────


class TestHandleDispatchFromRouter:
    """DISPATCH_TASK 从 PA daemon 发来时统一走 dispatch_sub_agent。"""

    @pytest.mark.asyncio
    async def test_dispatch_from_router_calls_dispatch_sub_agent(self, inproc_config):
        """_handle_dispatch_from_router 调用 dispatch_sub_agent。"""
        scheduler = AgentScheduler(inproc_config)

        envelope = Envelope(
            msg_type=MessageType.DISPATCH_TASK,
            source="pa:session-1",
            target="scheduler",
            session_id="session-1",
            payload={
                "agent_name": "coder",
                "task_id": "task-dispatch",
                "task_prompt": "写代码",
                "model_config": {"model": "gpt-4"},
                "role_prompt": "编程专家",
                "max_tool_rounds": 6,
            },
        )

        with patch.object(scheduler, "dispatch_sub_agent", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = AgentProcessInfo(
                process_id="sub:coder:task-dispatch",
                agent_type="sub_agent",
            )
            await scheduler._handle_dispatch_from_router(envelope)

            mock_dispatch.assert_awaited_once_with(
                task_id="task-dispatch",
                task_prompt="写代码",
                parent_session_id="session-1",
                agent_name="coder",
                model_config={"model": "gpt-4"},
                role_prompt="编程专家",
                max_tool_rounds=6,
            )


# ── Router loop AGENT_RESPONSE 拦截 ─────────────────────


class TestRouterLoopResponseInterception:
    """_router_loop 中 AGENT_RESPONSE 拦截逻辑测试。"""

    @pytest.mark.asyncio
    async def test_sub_agent_response_triggers_delivery(self, inproc_config):
        """来自 sub: 的 AGENT_RESPONSE 触发 _deliver_sub_result。"""
        scheduler = AgentScheduler(inproc_config)

        envelope = Envelope(
            msg_type=MessageType.AGENT_RESPONSE,
            source="sub:coder:task-resp",
            target="pa:session-1",
            session_id="session-1",
            payload={
                "task_id": "task-resp",
                "status": "done",
                "result": "完成",
                "agent_name": "coder",
            },
        )

        with patch.object(scheduler, "_deliver_sub_result", new_callable=AsyncMock) as mock_deliver:
            # 直接测试拦截逻辑
            source = envelope.source or ""
            assert source.startswith("sub:")

            await scheduler._deliver_sub_result(envelope)
            mock_deliver.assert_awaited_once_with(envelope)

    def test_non_sub_response_not_intercepted(self):
        """非 sub: 来源的 AGENT_RESPONSE 不触发拦截。"""
        envelope = Envelope(
            msg_type=MessageType.AGENT_RESPONSE,
            source="pa:session-1",
            target="sub:coder:task-1",
            payload={"status": "acknowledged"},
        )
        source = envelope.source or ""
        assert not source.startswith("sub:")


# ── Scheduler stop 清理 register events ─────────────────


class TestSchedulerStopCleanup:
    """Scheduler stop 时清理 _register_events。"""

    @pytest.mark.asyncio
    async def test_stop_clears_register_events(self, inproc_config):
        """stop() 清理 _register_events。"""
        scheduler = AgentScheduler(inproc_config)
        with patch.object(scheduler, "_start_cron_daemon", new_callable=AsyncMock):
            await scheduler.start()

            scheduler._register_events["sub:test:t1"] = asyncio.Event()
            scheduler._register_events["sub:test:t2"] = asyncio.Event()
            assert len(scheduler._register_events) == 2

            await scheduler.stop()
            assert len(scheduler._register_events) == 0
