"""SubAgent 单元测试。

覆盖范围：
- 状态机（run 成功/失败/运行中/日志保存）
- 执行日志（_log 结构、顺序、时间戳）
- 系统 Prompt（有/无 role_prompt）
- 记忆隔离（session_id 独立、多 agent 不互染）
- 工具调用流程（单轮、工具名/入参记录、duration_ms、工具出错继续流程）
- Coder SubAgent 场景（写文件+执行脚本、两轮工具、任务失败、完整日志结构）
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agentpal.agents.sub_agent import SubAgent
from agentpal.memory.buffer import BufferMemory
from agentpal.models.session import SubAgentTask, TaskStatus


def _make_task(
    task_id: str = "task-001",
    agent_name: str | None = None,
    task_type: str | None = None,
    priority: int = 5,
    max_retries: int = 3,
    retry_count: int = 0,
) -> SubAgentTask:
    return SubAgentTask(
        id=task_id,
        parent_session_id="parent-session",
        sub_session_id=f"sub:parent-session:{task_id}",
        task_prompt="执行测试任务",
        status=TaskStatus.PENDING,
        agent_name=agent_name,
        task_type=task_type,
        priority=priority,
        retry_count=retry_count,
        max_retries=max_retries,
    )


def _make_agent(
    mock_db: MagicMock,
    role_prompt: str = "",
    agent_name: str | None = None,
    task_type: str | None = None,
    task_id: str = "task-001",
) -> SubAgent:
    task = _make_task(task_id=task_id, agent_name=agent_name, task_type=task_type)
    memory = BufferMemory(max_size=10)
    return SubAgent(
        session_id=f"sub:parent-session:{task_id}",
        memory=memory,
        task=task,
        db=mock_db,
        model_config={"config_name": "test", "model_name": "test-model"},
        role_prompt=role_prompt,
    )


def _make_text_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.content = [{"type": "text", "text": text}]
    return resp


def _make_tool_call_block(
    tc_id: str = "call_001",
    name: str = "get_current_time",
    input_data: dict | None = None,
) -> dict:
    return {"type": "tool_use", "id": tc_id, "name": name, "input": input_data or {}}


def _make_tool_result_mock(text: str = "tool output") -> MagicMock:
    result = MagicMock()
    result.content = [{"type": "text", "text": text}]
    return result


@pytest.fixture
def mock_db() -> MagicMock:
    db = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


@pytest.fixture
def sub_agent(mock_db: MagicMock) -> SubAgent:
    return _make_agent(mock_db)


@pytest.fixture
def coder_agent(mock_db: MagicMock) -> SubAgent:
    return _make_agent(
        mock_db,
        role_prompt=(
            "你是一个专注的编码员。你的职责是：\n"
            "- 编写和调试代码\n"
            "- 执行脚本和命令\n"
            "- 技术实现和验证\n\n"
            "工作原则：优先用工具验证结果，不要只给理论答案。代码需经过测试。"
        ),
        agent_name="coder",
        task_type="code",
    )


# ── 状态机 ───────────────────────────────────────────────────────────


class TestSubAgentRun:
    @pytest.mark.asyncio
    async def test_run_success_updates_status(self, sub_agent: SubAgent):
        """run 成功时状态变为 DONE，result 被写入。"""
        with patch.object(sub_agent, "reply", new_callable=AsyncMock, return_value="任务完成结果"):
            result = await sub_agent.run("执行任务")

        assert result == "任务完成结果"
        assert sub_agent._task.status == TaskStatus.DONE
        assert sub_agent._task.result == "任务完成结果"
        assert sub_agent._task.finished_at is not None

    @pytest.mark.asyncio
    async def test_run_failure_updates_status(self, sub_agent: SubAgent):
        """max_retries=3 → 首次失败进入重试（status=PENDING），不直接 FAILED。"""
        with (
            patch.object(sub_agent, "reply", side_effect=RuntimeError("LLM Error")),
            patch("agentpal.agents.sub_agent.asyncio.create_task") as mock_create_task,
        ):
            result = await sub_agent.run("执行任务")

        assert result == ""
        # 首次失败且 retry_count < max_retries → 进入重试
        assert sub_agent._task.status == TaskStatus.PENDING
        assert sub_agent._task.retry_count == 1
        mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_sets_running_before_reply(self, sub_agent: SubAgent):
        """reply 执行期间任务状态应为 RUNNING。"""
        statuses = []

        async def capture_reply(_prompt, **_kwargs):
            statuses.append(sub_agent._task.status)
            return "done"

        with patch.object(sub_agent, "reply", side_effect=capture_reply):
            await sub_agent.run("task")

        assert statuses[0] == TaskStatus.RUNNING

    @pytest.mark.asyncio
    async def test_run_saves_execution_log_on_success(self, sub_agent: SubAgent):
        """run 成功后 execution_log 应同步到 task。"""

        async def inject_log(_prompt, **_kwargs):
            sub_agent._execution_log.append({"type": "custom", "data": "x"})
            return "ok"

        with patch.object(sub_agent, "reply", side_effect=inject_log):
            await sub_agent.run("task")

        assert len(sub_agent._task.execution_log) == 1
        assert sub_agent._task.execution_log[0]["type"] == "custom"

    @pytest.mark.asyncio
    async def test_run_saves_execution_log_on_failure(self, sub_agent: SubAgent):
        """run 失败后 execution_log 同样应被保存。"""
        # 设置 max_retries=0，使失败直接标记 FAILED 而非进入重试
        sub_agent._task.max_retries = 0

        async def inject_then_fail(_prompt, **_kwargs):
            sub_agent._execution_log.append({"type": "partial", "data": "before_fail"})
            raise ValueError("boom")

        with patch.object(sub_agent, "reply", side_effect=inject_then_fail):
            await sub_agent.run("task")

        assert sub_agent._task.status == TaskStatus.FAILED
        assert len(sub_agent._task.execution_log) == 1

    @pytest.mark.asyncio
    async def test_run_returns_empty_string_on_exception(self, sub_agent: SubAgent):
        """run 遭遇异常时返回空字符串，不向外抛出。"""
        with patch.object(sub_agent, "reply", new_callable=AsyncMock, side_effect=Exception("crash")):
            result = await sub_agent.run("task")

        assert result == ""


# ── 执行日志 ─────────────────────────────────────────────────────────


class TestSubAgentExecutionLog:
    def test_log_appends_entry(self, sub_agent: SubAgent):
        sub_agent._log("test_event", {"key": "value"})

        assert len(sub_agent._execution_log) == 1
        entry = sub_agent._execution_log[0]
        assert entry["type"] == "test_event"
        assert entry["key"] == "value"
        assert "timestamp" in entry

    def test_log_multiple_entries_ordered(self, sub_agent: SubAgent):
        sub_agent._log("event_1", {"seq": 1})
        sub_agent._log("event_2", {"seq": 2})
        sub_agent._log("event_3", {"seq": 3})

        assert [e["type"] for e in sub_agent._execution_log] == [
            "event_1", "event_2", "event_3"
        ]

    def test_log_timestamp_is_iso_format(self, sub_agent: SubAgent):
        sub_agent._log("ts_test", {})

        ts = sub_agent._execution_log[0]["timestamp"]
        assert "T" in ts  # ISO 8601 用 T 分隔日期和时间

    def test_log_initial_state_empty(self, sub_agent: SubAgent):
        assert sub_agent._execution_log == []


# ── 系统 Prompt ───────────────────────────────────────────────────────


class TestSubAgentSystemPrompt:
    def test_default_prompt_without_role(self, sub_agent: SubAgent):
        """无 role_prompt 时应使用通用任务执行器描述。"""
        prompt = sub_agent._build_sub_system_prompt()

        assert "专注的任务执行代理" in prompt
        assert "工作原则" in prompt

    def test_custom_role_prompt_included(self, coder_agent: SubAgent):
        """自定义 role_prompt 应体现在 system prompt 中。"""
        prompt = coder_agent._build_sub_system_prompt()

        assert "编码员" in prompt
        assert "你的角色" in prompt
        assert "工作原则" in prompt

    def test_prompt_has_separator(self, coder_agent: SubAgent):
        """角色描述与工作原则之间应有分隔线。"""
        prompt = coder_agent._build_sub_system_prompt()

        assert "---" in prompt

    def test_empty_role_prompt_uses_default(self, mock_db: MagicMock):
        """role_prompt 为空字符串时仍使用通用默认描述。"""
        agent = _make_agent(mock_db, role_prompt="")
        prompt = agent._build_sub_system_prompt()

        assert "专注的任务执行代理" in prompt


# ── 记忆隔离 ─────────────────────────────────────────────────────────


class TestSubAgentMemoryIsolation:
    def test_sub_agent_has_own_session(self, sub_agent: SubAgent):
        assert sub_agent.session_id == "sub:parent-session:task-001"

    def test_two_agents_have_distinct_sessions(self, mock_db: MagicMock):
        agent_a = _make_agent(mock_db, task_id="task-a")
        agent_b = _make_agent(mock_db, task_id="task-b")

        assert agent_a.session_id != agent_b.session_id

    @pytest.mark.asyncio
    async def test_sub_agent_writes_to_own_memory(self, sub_agent: SubAgent):
        """reply 完成后，记忆中应同时存在 user 和 assistant 消息。"""
        mock_response = _make_text_response("I did the task")
        mock_model = AsyncMock(return_value=mock_response)

        with (
            patch("agentpal.agents.personal_assistant._build_model", return_value=mock_model),
            patch.object(sub_agent, "_build_toolkit", new_callable=AsyncMock, return_value=None),
            patch.object(sub_agent, "_check_incoming_messages", new_callable=AsyncMock, return_value=[]),
        ):
            await sub_agent.reply("do something")

        messages = await sub_agent.memory.get_recent(sub_agent.session_id)
        roles = [str(m.role) for m in messages]
        assert "user" in roles
        assert "assistant" in roles

    @pytest.mark.asyncio
    async def test_two_agents_memory_not_shared(self, mock_db: MagicMock):
        """两个 SubAgent 的记忆互不影响。"""
        agent_a = _make_agent(mock_db, task_id="task-a")
        agent_b = _make_agent(mock_db, task_id="task-b")

        mock_response = _make_text_response("response for A")
        mock_model = AsyncMock(return_value=mock_response)

        with (
            patch("agentpal.agents.personal_assistant._build_model", return_value=mock_model),
            patch.object(agent_a, "_build_toolkit", new_callable=AsyncMock, return_value=None),
            patch.object(agent_a, "_check_incoming_messages", new_callable=AsyncMock, return_value=[]),
        ):
            await agent_a.reply("question for A")

        # agent_b 的记忆不应受 agent_a 影响
        msgs_b = await agent_b.memory.get_recent(agent_b.session_id)
        assert len(msgs_b) == 0


# ── 工具调用流程 ──────────────────────────────────────────────────────


class TestSubAgentToolCallFlow:
    @pytest.mark.asyncio
    async def test_reply_with_one_tool_call_round(self, sub_agent: SubAgent):
        """LLM → 工具调用 → 工具结果 → 最终回复 的完整单轮流程。"""
        first_response = MagicMock()
        first_response.content = [_make_tool_call_block("call_001", "get_current_time")]

        final_response = _make_text_response("当前时间是 12:00")
        mock_model = AsyncMock(side_effect=[first_response, final_response])

        tool_result = _make_tool_result_mock("2026-03-16 12:00:00")

        async def fake_gen():
            yield tool_result

        mock_toolkit = MagicMock()
        mock_toolkit.get_json_schemas.return_value = [{"name": "get_current_time"}]
        mock_toolkit.call_tool_function = AsyncMock(return_value=fake_gen())

        with (
            patch("agentpal.agents.personal_assistant._build_model", return_value=mock_model),
            patch.object(sub_agent, "_build_toolkit", new_callable=AsyncMock, return_value=mock_toolkit),
            patch.object(sub_agent, "_check_incoming_messages", new_callable=AsyncMock, return_value=[]),
        ):
            result = await sub_agent.reply("现在几点？")

        assert "12:00" in result
        log_types = [e["type"] for e in sub_agent._execution_log]
        assert "tool_start" in log_types
        assert "tool_done" in log_types
        assert "final_result" in log_types

    @pytest.mark.asyncio
    async def test_reply_tool_call_records_name_and_input(self, sub_agent: SubAgent):
        """tool_start 日志应记录正确的工具名称和输入参数。"""
        first_response = MagicMock()
        first_response.content = [
            _make_tool_call_block("call_002", "read_file", {"path": "/tmp/test.txt"})
        ]
        final_response = _make_text_response("文件读取完毕")
        mock_model = AsyncMock(side_effect=[first_response, final_response])

        tool_result = _make_tool_result_mock("hello world")

        async def fake_gen():
            yield tool_result

        mock_toolkit = MagicMock()
        mock_toolkit.get_json_schemas.return_value = []
        mock_toolkit.call_tool_function = AsyncMock(return_value=fake_gen())

        with (
            patch("agentpal.agents.personal_assistant._build_model", return_value=mock_model),
            patch.object(sub_agent, "_build_toolkit", new_callable=AsyncMock, return_value=mock_toolkit),
            patch.object(sub_agent, "_check_incoming_messages", new_callable=AsyncMock, return_value=[]),
        ):
            await sub_agent.reply("读取文件")

        tool_start = next(e for e in sub_agent._execution_log if e["type"] == "tool_start")
        assert tool_start["name"] == "read_file"
        assert tool_start["input"] == {"path": "/tmp/test.txt"}

    @pytest.mark.asyncio
    async def test_reply_tool_done_has_duration_ms(self, sub_agent: SubAgent):
        """tool_done 日志应包含 duration_ms 且值 >= 0。"""
        first_response = MagicMock()
        first_response.content = [_make_tool_call_block()]
        final_response = _make_text_response("done")
        mock_model = AsyncMock(side_effect=[first_response, final_response])

        tool_result = _make_tool_result_mock("ok")

        async def fake_gen():
            yield tool_result

        mock_toolkit = MagicMock()
        mock_toolkit.get_json_schemas.return_value = []
        mock_toolkit.call_tool_function = AsyncMock(return_value=fake_gen())

        with (
            patch("agentpal.agents.personal_assistant._build_model", return_value=mock_model),
            patch.object(sub_agent, "_build_toolkit", new_callable=AsyncMock, return_value=mock_toolkit),
            patch.object(sub_agent, "_check_incoming_messages", new_callable=AsyncMock, return_value=[]),
        ):
            await sub_agent.reply("test")

        tool_done = next(e for e in sub_agent._execution_log if e["type"] == "tool_done")
        assert "duration_ms" in tool_done
        assert tool_done["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_reply_tool_error_captured_in_log(self, sub_agent: SubAgent):
        """工具执行异常时，error 字段记录在 tool_done，流程继续返回 LLM 最终回复。"""
        first_response = MagicMock()
        first_response.content = [
            _make_tool_call_block("call_err", "execute_shell_command", {"command": "rm -rf /"})
        ]
        final_response = _make_text_response("命令执行失败，已处理错误")
        mock_model = AsyncMock(side_effect=[first_response, final_response])

        # call_tool_function 本身抛异常
        mock_toolkit = MagicMock()
        mock_toolkit.get_json_schemas.return_value = []
        mock_toolkit.call_tool_function = AsyncMock(side_effect=RuntimeError("Permission denied"))

        with (
            patch("agentpal.agents.personal_assistant._build_model", return_value=mock_model),
            patch.object(sub_agent, "_build_toolkit", new_callable=AsyncMock, return_value=mock_toolkit),
            patch.object(sub_agent, "_check_incoming_messages", new_callable=AsyncMock, return_value=[]),
        ):
            result = await sub_agent.reply("执行命令")

        tool_done = next(e for e in sub_agent._execution_log if e["type"] == "tool_done")
        assert tool_done["error"] is not None
        assert "Permission denied" in tool_done["error"]
        # 工具失败后 LLM 最终回复仍应被返回
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_reply_no_tool_call_model_called_once(self, sub_agent: SubAgent):
        """LLM 不触发工具调用时，model 仅被调用一次。"""
        only_response = _make_text_response("直接回答，无需工具")
        mock_model = AsyncMock(return_value=only_response)

        mock_toolkit = MagicMock()
        mock_toolkit.get_json_schemas.return_value = []

        with (
            patch("agentpal.agents.personal_assistant._build_model", return_value=mock_model),
            patch.object(sub_agent, "_build_toolkit", new_callable=AsyncMock, return_value=mock_toolkit),
            patch.object(sub_agent, "_check_incoming_messages", new_callable=AsyncMock, return_value=[]),
        ):
            result = await sub_agent.reply("你好")

        assert mock_model.call_count == 1
        assert "直接回答" in result


# ── Coder SubAgent 场景 ──────────────────────────────────────────────


class TestSubAgentCoderScenario:
    """Coder SubAgent 专项场景测试，模拟编码任务全流程。"""

    def test_coder_system_prompt_contains_coding_keywords(self, coder_agent: SubAgent):
        """coder 的 system prompt 必须包含编码相关关键词。"""
        prompt = coder_agent._build_sub_system_prompt()

        assert "编码员" in prompt
        assert "代码" in prompt
        assert "工具验证" in prompt

    def test_coder_task_metadata(self, coder_agent: SubAgent):
        """coder task 应携带正确的 agent_name 和 task_type。"""
        assert coder_agent._task.agent_name == "coder"
        assert coder_agent._task.task_type == "code"

    @pytest.mark.asyncio
    async def test_coder_run_code_task_success(self, coder_agent: SubAgent):
        """coder 执行代码任务成功的完整路径。"""
        expected = "代码执行完毕，pytest 测试全部通过（5 passed）。"

        with patch.object(coder_agent, "reply", new_callable=AsyncMock, return_value=expected):
            result = await coder_agent.run("写一个计算斐波那契数列的函数并运行测试")

        assert result == expected
        assert coder_agent._task.status == TaskStatus.DONE
        assert coder_agent._task.agent_name == "coder"
        assert coder_agent._task.task_type == "code"

    @pytest.mark.asyncio
    async def test_coder_uses_shell_tool_for_script(self, coder_agent: SubAgent):
        """coder 通过 execute_shell_command 执行脚本，日志记录工具调用详情。"""
        first_response = MagicMock()
        first_response.content = [
            _make_tool_call_block(
                "shell_001",
                "execute_shell_command",
                {"command": "python -c \"print('hello')\""},
            )
        ]
        final_response = _make_text_response("脚本执行成功，输出：hello")
        mock_model = AsyncMock(side_effect=[first_response, final_response])

        shell_result = _make_tool_result_mock("hello")

        async def fake_gen():
            yield shell_result

        mock_toolkit = MagicMock()
        mock_toolkit.get_json_schemas.return_value = [{"name": "execute_shell_command"}]
        mock_toolkit.call_tool_function = AsyncMock(return_value=fake_gen())

        with (
            patch("agentpal.agents.personal_assistant._build_model", return_value=mock_model),
            patch.object(coder_agent, "_build_toolkit", new_callable=AsyncMock, return_value=mock_toolkit),
            patch.object(coder_agent, "_check_incoming_messages", new_callable=AsyncMock, return_value=[]),
        ):
            result = await coder_agent.reply("运行一个 Hello World 脚本")

        assert "hello" in result.lower() or "成功" in result

        log_types = [e["type"] for e in coder_agent._execution_log]
        assert "tool_start" in log_types
        assert "tool_done" in log_types

        tool_start = next(e for e in coder_agent._execution_log if e["type"] == "tool_start")
        assert tool_start["name"] == "execute_shell_command"
        assert "python" in tool_start["input"].get("command", "")

    @pytest.mark.asyncio
    async def test_coder_write_then_run_two_tool_rounds(self, coder_agent: SubAgent):
        """coder 先写文件再执行脚本——两轮工具调用，日志均有记录。"""
        # 第 1 轮：写文件
        write_response = MagicMock()
        write_response.content = [
            _make_tool_call_block(
                "write_001", "write_file",
                {"path": "/tmp/fib.py", "content": "def fib(n): ..."},
            )
        ]
        write_result = _make_tool_result_mock("文件写入成功")

        # 第 2 轮：执行脚本
        run_response = MagicMock()
        run_response.content = [
            _make_tool_call_block(
                "shell_002", "execute_shell_command",
                {"command": "python /tmp/fib.py"},
            )
        ]
        run_result = _make_tool_result_mock("0 1 1 2 3 5 8 13")

        # 第 3 轮：最终总结（无工具调用）
        final_response = _make_text_response("斐波那契函数已写入并测试通过")
        mock_model = AsyncMock(side_effect=[write_response, run_response, final_response])

        # 每次调用返回对应的 gen
        tool_results = [write_result, run_result]
        call_idx = [0]

        async def dynamic_call_tool(_tc):
            result = tool_results[call_idx[0]]
            call_idx[0] += 1

            async def _gen():
                yield result

            return _gen()

        mock_toolkit = MagicMock()
        mock_toolkit.get_json_schemas.return_value = []
        mock_toolkit.call_tool_function = dynamic_call_tool

        with (
            patch("agentpal.agents.personal_assistant._build_model", return_value=mock_model),
            patch.object(coder_agent, "_build_toolkit", new_callable=AsyncMock, return_value=mock_toolkit),
            patch.object(coder_agent, "_check_incoming_messages", new_callable=AsyncMock, return_value=[]),
        ):
            result = await coder_agent.reply("写一个斐波那契函数并运行验证")

        assert "斐波那契" in result or "0 1 1 2" in result

        tool_starts = [e for e in coder_agent._execution_log if e["type"] == "tool_start"]
        assert len(tool_starts) == 2
        tool_names = {e["name"] for e in tool_starts}
        assert "write_file" in tool_names
        assert "execute_shell_command" in tool_names

    @pytest.mark.asyncio
    async def test_coder_task_failure_records_error(self, coder_agent: SubAgent):
        """coder 任务失败时，error 字段应包含异常类型和消息。"""
        # 设置 max_retries=0，使失败直接标记 FAILED 而非进入重试
        coder_agent._task.max_retries = 0
        with patch.object(
            coder_agent, "reply",
            new_callable=AsyncMock,
            side_effect=Exception("SyntaxError in generated code"),
        ):
            result = await coder_agent.run("写一段有问题的代码")

        assert result == ""
        assert coder_agent._task.status == TaskStatus.FAILED
        assert "SyntaxError" in coder_agent._task.error
        assert coder_agent._task.finished_at is not None

    @pytest.mark.asyncio
    async def test_coder_execution_log_complete_structure(self, coder_agent: SubAgent):
        """coder reply 执行日志应包含 system_prompt、user_message、llm_response、final_result。"""
        response = _make_text_response("代码分析完成")
        mock_model = AsyncMock(return_value=response)

        with (
            patch("agentpal.agents.personal_assistant._build_model", return_value=mock_model),
            patch.object(coder_agent, "_build_toolkit", new_callable=AsyncMock, return_value=None),
            patch.object(coder_agent, "_check_incoming_messages", new_callable=AsyncMock, return_value=[]),
        ):
            await coder_agent.reply("分析代码质量")

        log_types = [e["type"] for e in coder_agent._execution_log]
        assert "system_prompt" in log_types
        assert "user_message" in log_types
        assert "llm_response" in log_types
        assert "final_result" in log_types

        user_msgs = await coder_agent.memory.get_recent(coder_agent.session_id)
        roles = [m.role for m in user_msgs]
        assert "user" in [str(r) for r in roles]
        assert "assistant" in [str(r) for r in roles]


class TestSubAgentRetry:
    """SubAgent 自动重试逻辑。"""

    @pytest.mark.asyncio
    async def test_retry_increments_count(self, mock_db):
        """失败时 retry_count 递增，状态重置为 PENDING。"""
        task = _make_task(max_retries=3, retry_count=0)
        agent = SubAgent(
            session_id="sub:p:retry-1",
            memory=BufferMemory(max_size=10),
            task=task,
            db=mock_db,
        )

        with (
            patch.object(agent, "reply", side_effect=RuntimeError("fail")),
            patch("agentpal.agents.sub_agent.asyncio.create_task"),
        ):
            await agent.run("task")

        assert task.retry_count == 1
        assert task.status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_max_retries_zero_fails_immediately(self, mock_db):
        """max_retries=0 时失败直接标记 FAILED，不重试。"""
        task = _make_task(max_retries=0, retry_count=0)
        agent = SubAgent(
            session_id="sub:p:no-retry",
            memory=BufferMemory(max_size=10),
            task=task,
            db=mock_db,
        )

        with patch.object(agent, "reply", side_effect=RuntimeError("fail")):
            await agent.run("task")

        assert task.status == TaskStatus.FAILED
        assert task.retry_count == 0
        assert "RuntimeError" in task.error

    @pytest.mark.asyncio
    async def test_exhausted_retries_marks_failed(self, mock_db):
        """retry_count == max_retries 时应标记 FAILED。"""
        task = _make_task(max_retries=2, retry_count=2)
        agent = SubAgent(
            session_id="sub:p:exhausted",
            memory=BufferMemory(max_size=10),
            task=task,
            db=mock_db,
        )

        with patch.object(agent, "reply", side_effect=RuntimeError("fail")):
            await agent.run("task")

        assert task.status == TaskStatus.FAILED
        assert task.retry_count == 2  # 不再递增

    @pytest.mark.asyncio
    async def test_execution_log_preserved_across_retries(self, mock_db):
        """重试时 execution_log 应保留之前的记录。"""
        task = _make_task(max_retries=3, retry_count=0)
        agent = SubAgent(
            session_id="sub:p:log-retry",
            memory=BufferMemory(max_size=10),
            task=task,
            db=mock_db,
        )

        with (
            patch.object(agent, "reply", side_effect=RuntimeError("fail")),
            patch("agentpal.agents.sub_agent.asyncio.create_task"),
        ):
            await agent.run("task")

        log_types = [entry["type"] for entry in task.execution_log]
        assert "retry_scheduled" in log_types


class TestSubAgentPriority:
    """SubAgent 优先级字段。"""

    def test_default_priority(self):
        """默认优先级为 5。"""
        task = _make_task()
        assert task.priority == 5

    def test_custom_priority(self):
        """支持自定义优先级。"""
        task = _make_task(priority=9)
        assert task.priority == 9

    def test_default_max_retries(self):
        """默认 max_retries 为 3。"""
        task = _make_task()
        assert task.max_retries == 3

    def test_custom_max_retries(self):
        """支持自定义 max_retries。"""
        task = _make_task(max_retries=0)
        assert task.max_retries == 0

    def test_priority_sorting(self):
        """高优先级任务排在前面。"""
        tasks = [
            _make_task(task_id="low", priority=1),
            _make_task(task_id="high", priority=10),
            _make_task(task_id="mid", priority=5),
        ]
        sorted_tasks = sorted(tasks, key=lambda t: t.priority, reverse=True)
        assert sorted_tasks[0].id == "high"
        assert sorted_tasks[1].id == "mid"
        assert sorted_tasks[2].id == "low"
