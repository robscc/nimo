"""E2E 测试：SubAgent 自动调度 LLM 行为验证。

测试在不同 sub_agent_mode 下，PersonalAssistant 的实际 LLM 行为：
  - mode=auto: 复杂任务应触发 dispatch_sub_agent 工具调用
  - mode=off:  复杂任务不应触发 SubAgent 调度
  - mode=manual: 仅 @mention 时触发

注意：
  - 这些测试调用真实 LLM，耗时较长（30-90s），标记为 @pytest.mark.llm
  - LLM 行为有不确定性，测试设计允许一定的容错
  - 使用 trust_env=False 避免系统代理干扰

运行方式：
  cd backend && .venv/bin/pytest tests/e2e/test_sub_agent_dispatch_e2e.py -v --tb=short

依赖：后端运行在 http://localhost:8099，需配置有效 LLM API Key。
"""

from __future__ import annotations

import httpx
import pytest

from tests.e2e.helpers import (
    assert_chat_completed,
    create_session,
    extract_reply_text,
    get_tool_calls,
    require_backend,
    send_chat,
    set_sub_agent_mode,
)

pytestmark = [pytest.mark.e2e, pytest.mark.llm, pytest.mark.slow, require_backend]


class TestSubAgentAutoDispatch:
    """sub_agent_mode=auto 模式下的自动调度行为。"""

    def test_simple_question_no_dispatch(self, chat_client: httpx.Client):
        """简单问题不应触发 SubAgent 调度。"""
        session_id = create_session(chat_client, channel="test")
        set_sub_agent_mode(chat_client, session_id, "auto")

        events = send_chat(chat_client, session_id, "1+1等于几？请简短回答。")
        assert_chat_completed(events)

        tool_calls = get_tool_calls(events)
        dispatch_calls = [tc for tc in tool_calls if tc.get("name") == "dispatch_sub_agent"]
        assert len(dispatch_calls) == 0, (
            f"简单问题不应触发 SubAgent 调度，但发现: {dispatch_calls}"
        )

        reply = extract_reply_text(events)
        assert len(reply) > 0, "应有回复文本"
        assert "2" in reply, f"回复应包含 '2'，实际: {reply}"

    def test_complex_task_may_dispatch(self, chat_client: httpx.Client):
        """复杂研究型任务在 auto 模式下可能触发 SubAgent 调度。

        注意：LLM 决策有不确定性，此测试不强制要求必须触发。
        重点验证对话正常完成、不报错。
        """
        session_id = create_session(chat_client, channel="test")
        set_sub_agent_mode(chat_client, session_id, "auto")

        events = send_chat(
            chat_client,
            session_id,
            "请帮我详细研究 Python asyncio 和 Go goroutine 的性能对比，"
            "写一份包含基准测试数据的分析报告。",
            timeout=90.0,
        )
        assert_chat_completed(events)

        reply = extract_reply_text(events)
        assert len(reply) > 0, "应有回复文本"

        # 不强制断言 dispatch，只记录是否触发
        tool_calls = get_tool_calls(events)
        dispatch_calls = [tc for tc in tool_calls if tc.get("name") == "dispatch_sub_agent"]
        if dispatch_calls:
            print(f"[INFO] 自动模式下触发了 SubAgent 调度: {len(dispatch_calls)} 次")
            for dc in dispatch_calls:
                print(f"  - input: {dc.get('input', {})}")


class TestSubAgentOffMode:
    """sub_agent_mode=off 模式下禁用调度。"""

    def test_off_mode_no_dispatch(self, chat_client: httpx.Client):
        """off 模式下不应触发 SubAgent 调度，即使是复杂任务。"""
        session_id = create_session(chat_client, channel="test")
        set_sub_agent_mode(chat_client, session_id, "off")

        events = send_chat(
            chat_client,
            session_id,
            "请研究 Rust 和 C++ 在嵌入式系统中的性能对比，写一份详细报告。",
            timeout=90.0,
        )
        assert_chat_completed(events)

        tool_calls = get_tool_calls(events)
        dispatch_calls = [tc for tc in tool_calls if tc.get("name") == "dispatch_sub_agent"]
        assert len(dispatch_calls) == 0, (
            f"off 模式不应触发 SubAgent 调度，但发现: {dispatch_calls}"
        )

        reply = extract_reply_text(events)
        assert len(reply) > 0, "off 模式下仍应有回复"

    def test_off_mode_still_responds(self, chat_client: httpx.Client):
        """off 模式下主 Agent 应直接回复，不依赖 SubAgent。"""
        session_id = create_session(chat_client, channel="test")
        set_sub_agent_mode(chat_client, session_id, "off")

        events = send_chat(
            chat_client,
            session_id,
            "你好，请介绍一下你自己。",
        )
        assert_chat_completed(events)

        reply = extract_reply_text(events)
        assert len(reply) > 10, f"应有有意义的回复，实际: {reply}"


class TestSubAgentManualMode:
    """sub_agent_mode=manual 模式下的 @mention 触发。"""

    def test_manual_mode_normal_chat_no_dispatch(self, chat_client: httpx.Client):
        """manual 模式下普通对话不应触发 SubAgent 调度。"""
        session_id = create_session(chat_client, channel="test")
        set_sub_agent_mode(chat_client, session_id, "manual")

        events = send_chat(
            chat_client,
            session_id,
            "请帮我研究一下 Python 3.12 的新特性。",
            timeout=60.0,
        )
        assert_chat_completed(events)

        tool_calls = get_tool_calls(events)
        dispatch_calls = [tc for tc in tool_calls if tc.get("name") == "dispatch_sub_agent"]
        assert len(dispatch_calls) == 0, (
            f"manual 模式下普通对话不应触发 dispatch，但发现: {dispatch_calls}"
        )

        reply = extract_reply_text(events)
        assert len(reply) > 0, "应有回复"


class TestSubAgentNullMode:
    """sub_agent_mode=null（跟随全局）模式。"""

    def test_null_mode_chat_works(self, chat_client: httpx.Client):
        """null 模式下对话应正常工作。"""
        session_id = create_session(chat_client, channel="test")
        # 不设置 mode，使用默认的 null

        events = send_chat(
            chat_client,
            session_id,
            "请用一句话回答：什么是 HTTP？",
        )
        assert_chat_completed(events)

        reply = extract_reply_text(events)
        assert len(reply) > 0, "应有回复"
        # HTTP 相关回复应包含关键词
        reply_lower = reply.lower()
        assert "http" in reply_lower or "协议" in reply or "protocol" in reply_lower, (
            f"回复应与 HTTP 相关，实际: {reply}"
        )


class TestSubAgentDispatchAPI:
    """SubAgent dispatch API 端点测试。"""

    def test_dispatch_creates_task(self, api_client: httpx.Client):
        """POST /api/v1/agent/dispatch 应创建任务并返回任务信息。"""
        session_id = create_session(api_client, channel="test")

        resp = api_client.post(
            "/api/v1/agent/dispatch",
            json={
                "parent_session_id": session_id,
                "task_prompt": "测试任务：简单回答 1+1",
                "task_type": "research",
            },
            timeout=30.0,
        )
        assert resp.is_success, f"dispatch 失败: {resp.status_code} {resp.text}"
        data = resp.json()

        assert "task_id" in data
        assert data["status"] in ("pending", "running", "completed", "failed")
        assert data.get("task_type") == "research"

    def test_get_task_status(self, api_client: httpx.Client):
        """GET /api/v1/agent/tasks/{task_id} 应返回任务状态。"""
        session_id = create_session(api_client, channel="test")

        # 创建任务
        resp = api_client.post(
            "/api/v1/agent/dispatch",
            json={
                "parent_session_id": session_id,
                "task_prompt": "测试任务：说你好",
                "agent_name": "researcher",
            },
            timeout=30.0,
        )
        assert resp.is_success
        task_id = resp.json()["task_id"]

        # 查询状态
        resp2 = api_client.get(f"/api/v1/agent/tasks/{task_id}")
        assert resp2.is_success, f"查询任务失败: {resp2.status_code} {resp2.text}"
        data = resp2.json()
        assert data["task_id"] == task_id

    def test_list_tasks(self, api_client: httpx.Client):
        """GET /api/v1/agent/tasks 应返回任务列表。"""
        session_id = create_session(api_client, channel="test")

        # 创建一个任务
        resp = api_client.post(
            "/api/v1/agent/dispatch",
            json={
                "parent_session_id": session_id,
                "task_prompt": "列表测试任务",
            },
            timeout=30.0,
        )
        assert resp.is_success

        # 列出任务
        resp2 = api_client.get(
            "/api/v1/agent/tasks",
            params={"parent_session_id": session_id},
        )
        assert resp2.is_success
        data = resp2.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 1

    def test_nonexistent_task_returns_404(self, api_client: httpx.Client):
        """查询不存在的任务应返回 404。"""
        resp = api_client.get("/api/v1/agent/tasks/nonexistent-task-id-12345")
        assert resp.status_code == 404


class TestSubAgentDefinitionsAPI:
    """SubAgent 定义 CRUD API 测试。"""

    def test_list_sub_agents(self, api_client: httpx.Client):
        """GET /api/v1/sub-agents 应返回 SubAgent 列表。"""
        resp = api_client.get("/api/v1/sub-agents")
        assert resp.is_success
        agents = resp.json()
        assert isinstance(agents, list)
        # 应至少有默认的 researcher 和 coder
        names = [a["name"] for a in agents]
        assert "researcher" in names, f"应有默认 researcher agent，实际: {names}"
        assert "coder" in names, f"应有默认 coder agent，实际: {names}"

    def test_get_sub_agent_by_name(self, api_client: httpx.Client):
        """GET /api/v1/sub-agents/{name} 应返回指定 SubAgent。"""
        resp = api_client.get("/api/v1/sub-agents/researcher")
        assert resp.is_success
        agent = resp.json()
        assert agent["name"] == "researcher"
        assert "role_prompt" in agent
        assert "accepted_task_types" in agent

    def test_get_nonexistent_sub_agent_returns_404(self, api_client: httpx.Client):
        """获取不存在的 SubAgent 应返回 404。"""
        resp = api_client.get("/api/v1/sub-agents/nonexistent-agent-xyz")
        assert resp.status_code == 404

    def test_sub_agent_has_expected_fields(self, api_client: httpx.Client):
        """SubAgent 定义应包含完整的配置字段。"""
        resp = api_client.get("/api/v1/sub-agents/researcher")
        assert resp.is_success
        agent = resp.json()

        expected_fields = [
            "name",
            "role_prompt",
            "accepted_task_types",
            "max_tool_rounds",
            "timeout_seconds",
            "enabled",
        ]
        for field in expected_fields:
            assert field in agent, f"SubAgent 应包含字段 '{field}'，实际字段: {list(agent.keys())}"
