"""Bootstrap + Heartbeat 功能的单元测试。"""

from __future__ import annotations

import pytest

from agentpal.workspace.context_builder import ContextBuilder, WorkspaceFiles
from agentpal.workspace.defaults import DEFAULT_BOOTSTRAP_MD, DEFAULT_HEARTBEAT_MD, DEFAULT_FILES, EDITABLE_FILES


# ── WorkspaceFiles dataclass ────────────────────────────


class TestWorkspaceFilesDataclass:
    """验证 WorkspaceFiles 新增字段。"""

    def test_bootstrap_field_default(self):
        ws = WorkspaceFiles()
        assert ws.bootstrap == ""

    def test_heartbeat_field_default(self):
        ws = WorkspaceFiles()
        assert ws.heartbeat == ""

    def test_bootstrap_field_set(self):
        ws = WorkspaceFiles(bootstrap="# Bootstrap content")
        assert ws.bootstrap == "# Bootstrap content"

    def test_heartbeat_field_set(self):
        ws = WorkspaceFiles(heartbeat="- 检查记忆")
        assert ws.heartbeat == "- 检查记忆"

    def test_all_fields_settable(self):
        ws = WorkspaceFiles(
            agents="agents",
            identity="identity",
            soul="soul",
            user="user",
            memory="memory",
            context="context",
            today_log="log",
            bootstrap="bootstrap",
            heartbeat="heartbeat",
        )
        assert ws.bootstrap == "bootstrap"
        assert ws.heartbeat == "heartbeat"


# ── defaults.py 完整性 ──────────────────────────────────


class TestDefaultFiles:
    """验证 defaults.py 包含新模板。"""

    def test_bootstrap_in_defaults(self):
        assert "BOOTSTRAP.md" in DEFAULT_FILES

    def test_heartbeat_in_defaults(self):
        assert "HEARTBEAT.md" in DEFAULT_FILES

    def test_bootstrap_in_editable(self):
        assert "BOOTSTRAP.md" in EDITABLE_FILES

    def test_heartbeat_in_editable(self):
        assert "HEARTBEAT.md" in EDITABLE_FILES

    def test_bootstrap_content_not_empty(self):
        assert len(DEFAULT_BOOTSTRAP_MD.strip()) > 100

    def test_heartbeat_content_not_empty(self):
        assert len(DEFAULT_HEARTBEAT_MD.strip()) > 50

    def test_bootstrap_has_key_sections(self):
        assert "首次见面" in DEFAULT_BOOTSTRAP_MD
        assert "开始对话" in DEFAULT_BOOTSTRAP_MD
        assert "USER.md" in DEFAULT_BOOTSTRAP_MD
        assert "SOUL.md" in DEFAULT_BOOTSTRAP_MD
        assert "完成后" in DEFAULT_BOOTSTRAP_MD

    def test_heartbeat_has_key_sections(self):
        assert "Heartbeat" in DEFAULT_HEARTBEAT_MD
        assert "定期" in DEFAULT_HEARTBEAT_MD


# ── ContextBuilder: Bootstrap 注入 ──────────────────────


class TestContextBuilderBootstrap:
    """验证 ContextBuilder 对 Bootstrap 内容的处理。"""

    def test_no_bootstrap_when_empty(self):
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="I am nimo.")
        prompt = cb.build_system_prompt(ws)
        assert "Bootstrap" not in prompt

    def test_no_bootstrap_when_placeholder(self):
        """'(暂无)' 或 '(空)' 占位符不应触发 bootstrap。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="I am nimo.", bootstrap="(暂无)")
        prompt = cb.build_system_prompt(ws)
        assert "Bootstrap" not in prompt

        ws2 = WorkspaceFiles(identity="I am nimo.", bootstrap="(空)")
        prompt2 = cb.build_system_prompt(ws2)
        assert "Bootstrap" not in prompt2

    def test_bootstrap_injected_when_present(self):
        cb = ContextBuilder()
        ws = WorkspaceFiles(
            identity="I am nimo.",
            bootstrap="# Bootstrap\n\n你刚刚醒来，去认识用户吧。",
        )
        prompt = cb.build_system_prompt(ws)
        assert "Bootstrap — 首次引导" in prompt
        assert "你刚刚醒来" in prompt

    def test_bootstrap_appears_before_identity(self):
        """Bootstrap 应出现在 Identity 之前（优先级最高）。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(
            identity="I am nimo.",
            bootstrap="# Bootstrap content here",
        )
        prompt = cb.build_system_prompt(ws)
        bootstrap_pos = prompt.find("Bootstrap")
        identity_pos = prompt.find("Agent Identity")
        assert bootstrap_pos < identity_pos, "Bootstrap should appear before Identity"

    def test_bootstrap_with_full_default_content(self):
        """使用完整默认 BOOTSTRAP.md 内容测试注入。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(
            identity="I am nimo.",
            bootstrap=DEFAULT_BOOTSTRAP_MD,
        )
        prompt = cb.build_system_prompt(ws)
        assert "首次引导" in prompt
        assert "认识一下" in prompt


# ── ContextBuilder: Heartbeat 注入 ──────────────────────


class TestContextBuilderHeartbeat:
    """验证 ContextBuilder 对 Heartbeat 内容的处理。"""

    def test_no_heartbeat_when_empty(self):
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="I am nimo.")
        prompt = cb.build_system_prompt(ws)
        assert "Heartbeat" not in prompt

    def test_no_heartbeat_when_only_comments(self):
        """HEARTBEAT.md 只有注释行时不应注入。"""
        cb = ContextBuilder()
        heartbeat_only_comments = (
            "# Heartbeat — 定期检查清单\n"
            "# 示例：\n"
            "# - 检查记忆\n"
            "> 这是注释行\n"
        )
        ws = WorkspaceFiles(identity="I am nimo.", heartbeat=heartbeat_only_comments)
        prompt = cb.build_system_prompt(ws)
        assert "Heartbeat — 定期任务" not in prompt

    def test_heartbeat_injected_with_active_tasks(self):
        """有实际任务行时应注入 heartbeat。"""
        cb = ContextBuilder()
        heartbeat_content = (
            "# Heartbeat\n"
            "> 定期执行\n"
            "- 回顾最近的日志\n"
            "- 检查 USER.md\n"
        )
        ws = WorkspaceFiles(identity="I am nimo.", heartbeat=heartbeat_content)
        prompt = cb.build_system_prompt(ws)
        assert "Heartbeat — 定期任务" in prompt
        assert "回顾最近的日志" in prompt
        assert "检查 USER.md" in prompt

    def test_heartbeat_filters_comments(self):
        """注入时应过滤掉注释行和空行。"""
        cb = ContextBuilder()
        heartbeat_mixed = (
            "# Title\n"
            "# 这是注释\n"
            "> 这也是\n"
            "- 活跃任务1\n"
            "\n"
            "- 活跃任务2\n"
        )
        ws = WorkspaceFiles(heartbeat=heartbeat_mixed)
        prompt = cb.build_system_prompt(ws)
        assert "活跃任务1" in prompt
        assert "活跃任务2" in prompt
        assert "这是注释" not in prompt
        assert "这也是" not in prompt


# ── ContextBuilder: MAX_AGENTS_CHARS 增大 ───────────────


class TestContextBuilderAgentsLimit:
    """验证 AGENTS.md 字符限制已增大。"""

    def test_agents_limit_is_4000(self):
        assert ContextBuilder.MAX_AGENTS_CHARS == 4000

    def test_agents_not_truncated_at_2500(self):
        """2500 字符的 AGENTS.md 不应被截断。"""
        cb = ContextBuilder()
        agents_text = "x" * 2500
        ws = WorkspaceFiles(agents=agents_text)
        prompt = cb.build_system_prompt(ws)
        assert "已截断" not in prompt

    def test_agents_truncated_at_5000(self):
        """5000 字符的 AGENTS.md 应被截断。"""
        cb = ContextBuilder()
        agents_text = "x" * 5000
        ws = WorkspaceFiles(agents=agents_text)
        prompt = cb.build_system_prompt(ws)
        assert "已截断" in prompt


# ── WorkspaceManager load() 兼容性 ─────────────────────


class TestWorkspaceManagerCompatibility:
    """验证 WorkspaceManager.load() 返回的 WorkspaceFiles 包含新字段。"""

    @pytest.mark.asyncio
    async def test_load_returns_bootstrap_and_heartbeat(self, tmp_path):
        """bootstrap 和 heartbeat 的 workspace 文件能正确加载。"""
        from agentpal.workspace.manager import WorkspaceManager

        ws_manager = WorkspaceManager(tmp_path)
        # 手动创建 bootstrap 标记，跳过 bootstrap 过程
        (tmp_path / ".bootstrapped").write_text("1")
        (tmp_path / "memory").mkdir(exist_ok=True)
        (tmp_path / "canvas").mkdir(exist_ok=True)

        # 写入测试文件
        (tmp_path / "BOOTSTRAP.md").write_text("# Test bootstrap", encoding="utf-8")
        (tmp_path / "HEARTBEAT.md").write_text("- test heartbeat task", encoding="utf-8")

        ws = await ws_manager.load()
        assert ws.bootstrap == "# Test bootstrap"
        assert ws.heartbeat == "- test heartbeat task"

    @pytest.mark.asyncio
    async def test_load_returns_empty_when_files_missing(self, tmp_path):
        """缺少 BOOTSTRAP.md/HEARTBEAT.md 时应返回空字符串。"""
        from agentpal.workspace.manager import WorkspaceManager

        ws_manager = WorkspaceManager(tmp_path)
        (tmp_path / ".bootstrapped").write_text("1")
        (tmp_path / "memory").mkdir(exist_ok=True)
        (tmp_path / "canvas").mkdir(exist_ok=True)

        ws = await ws_manager.load()
        assert ws.bootstrap == ""
        assert ws.heartbeat == ""

    @pytest.mark.asyncio
    async def test_bootstrap_creates_default_files(self, tmp_path):
        """首次 bootstrap 应创建 BOOTSTRAP.md 和 HEARTBEAT.md。"""
        from agentpal.workspace.manager import WorkspaceManager

        ws_manager = WorkspaceManager(tmp_path)
        result = await ws_manager.bootstrap()
        assert result is True

        assert (tmp_path / "BOOTSTRAP.md").exists()
        assert (tmp_path / "HEARTBEAT.md").exists()

        bootstrap_content = (tmp_path / "BOOTSTRAP.md").read_text(encoding="utf-8")
        assert "首次见面" in bootstrap_content

        heartbeat_content = (tmp_path / "HEARTBEAT.md").read_text(encoding="utf-8")
        assert "Heartbeat" in heartbeat_content


# ── Config heartbeat 配置 ──────────────────────────────


class TestHeartbeatConfig:
    """验证 heartbeat 配置字段。"""

    def test_heartbeat_enabled_default(self):
        """heartbeat_enabled 默认为 True。"""
        from agentpal.config import Settings

        # 创建带最小配置的 Settings
        settings = Settings(
            llm_api_key="test",
            _env_file=None,
        )
        assert settings.heartbeat_enabled is True

    def test_heartbeat_interval_default(self):
        """heartbeat_interval_minutes 默认为 60。"""
        from agentpal.config import Settings

        settings = Settings(
            llm_api_key="test",
            _env_file=None,
        )
        assert settings.heartbeat_interval_minutes == 60
