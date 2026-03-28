"""ContextBuilder sub_agent_roster 注入 单元测试。"""

from __future__ import annotations

from agentpal.workspace.context_builder import ContextBuilder, WorkspaceFiles


class TestContextBuilderSubAgentsEmpty:
    """sub_agent_roster 为空时不注入。"""

    def test_empty_string(self):
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="I am an assistant.")
        prompt = cb.build_system_prompt(ws, sub_agent_roster="")
        assert "Available SubAgents" not in prompt

    def test_whitespace_only(self):
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="I am an assistant.")
        prompt = cb.build_system_prompt(ws, sub_agent_roster="   \n  ")
        assert "Available SubAgents" not in prompt

    def test_default_parameter(self):
        """不传 sub_agent_roster 参数时默认不注入。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="I am an assistant.")
        prompt = cb.build_system_prompt(ws)
        assert "Available SubAgents" not in prompt


class TestContextBuilderSubAgentsInjected:
    """non-empty sub_agent_roster 注入为 '# SubAgent Roster'。"""

    def test_section_injected(self):
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="I am an assistant.")
        section = (
            "## SubAgent Roster\n\n"
            "### 调研员 (`researcher`)\n"
            "- **Task types:** research, summarize\n"
        )
        prompt = cb.build_system_prompt(ws, sub_agent_roster=section)
        assert "# SubAgent Roster" in prompt
        assert "researcher" in prompt
        assert "research, summarize" in prompt

    def test_section_content_preserved(self):
        """注入内容应完整保留（strip 后）。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="I am an assistant.")
        section = "dispatch_sub_agent(agent_name=\"coder\", ...)"
        prompt = cb.build_system_prompt(ws, sub_agent_roster=section)
        assert 'dispatch_sub_agent(agent_name="coder", ...)' in prompt


class TestContextBuilderSubAgentsPosition:
    """sub_agent_roster 注入位置：在 Agent Configuration 之后、Available Tools 之前。"""

    def test_after_agent_configuration(self):
        """SubAgents section 在 Agent Configuration 之后。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(
            identity="I am an assistant.",
            agents="Route all tasks to appropriate agents.",
        )
        section = "Some SubAgent roster content"
        prompt = cb.build_system_prompt(ws, sub_agent_roster=section)

        agent_config_pos = prompt.find("# Agent Configuration")
        sub_agents_pos = prompt.find("# SubAgent Roster")

        assert agent_config_pos != -1, "Agent Configuration section should exist"
        assert sub_agents_pos != -1, "SubAgent Roster section should exist"
        assert sub_agents_pos > agent_config_pos, (
            "SubAgent Roster should appear after Agent Configuration"
        )

    def test_before_available_tools(self):
        """SubAgents section 在 Available Tools 之前。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="I am an assistant.")
        section = "Some SubAgent roster content"
        enabled_tools = ["read_file", "browser_use"]
        prompt = cb.build_system_prompt(
            ws, enabled_tools=enabled_tools, sub_agent_roster=section,
        )

        sub_agents_pos = prompt.find("# SubAgent Roster")
        tools_pos = prompt.find("# Available Tools")

        assert sub_agents_pos != -1, "SubAgent Roster section should exist"
        assert tools_pos != -1, "Available Tools section should exist"
        assert sub_agents_pos < tools_pos, (
            "SubAgent Roster should appear before Available Tools"
        )

    def test_between_agent_configuration_and_tools(self):
        """完整测试：Agent Configuration < Available SubAgents < Available Tools。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(
            identity="I am an assistant.",
            agents="Route config here.",
        )
        section = "SubAgent roster here"
        enabled_tools = ["get_current_time"]
        prompt = cb.build_system_prompt(
            ws, enabled_tools=enabled_tools, sub_agent_roster=section,
        )

        pos_config = prompt.find("# Agent Configuration")
        pos_sub = prompt.find("# SubAgent Roster")
        pos_tools = prompt.find("# Available Tools")

        assert pos_config < pos_sub < pos_tools, (
            f"Expected ordering: Agent Configuration ({pos_config}) "
            f"< Available SubAgents ({pos_sub}) "
            f"< Available Tools ({pos_tools})"
        )

    def test_without_agent_configuration(self):
        """没有 Agent Configuration 时 SubAgents 仍正常注入。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="I am an assistant.")  # no agents
        section = "SubAgent roster here"
        prompt = cb.build_system_prompt(ws, sub_agent_roster=section)

        assert "# Agent Configuration" not in prompt
        assert "# SubAgent Roster" in prompt
        assert "SubAgent roster here" in prompt
