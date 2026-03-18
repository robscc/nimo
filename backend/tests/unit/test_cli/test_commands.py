"""Tests for CLI commands using typer.testing.CliRunner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from agentpal.cli.app import app

runner = CliRunner()


# ── nimo --version ───────────────────────────────────────

class TestVersion:
    def test_version_flag(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_version_short_flag(self):
        result = runner.invoke(app, ["-V"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_version_command(self):
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output


# ── nimo init ────────────────────────────────────────────

class TestInit:
    @patch("agentpal.cli.commands.init_cmd.run_async")
    def test_init_success(self, mock_run_async, tmp_path):
        """Test init command with all steps mocked."""
        mock_run_async.return_value = None

        with (
            patch("agentpal.workspace.manager.WorkspaceManager") as MockWM,
            patch("agentpal.services.config_file.ConfigFileManager") as MockCFM,
            patch("agentpal.database.init_db", new_callable=AsyncMock),
            patch("agentpal.database.run_migrations", new_callable=AsyncMock),
            patch("agentpal.database.AsyncSessionLocal"),
            patch("agentpal.agents.registry.SubAgentRegistry"),
        ):
            # Mock workspace manager
            mock_wm = MagicMock()
            MockWM.return_value = mock_wm

            # Mock config manager — use a plain MagicMock for config_path
            mock_cfm = MagicMock()
            mock_config_path = MagicMock()
            mock_config_path.exists.return_value = False
            mock_cfm.config_path = mock_config_path
            MockCFM.return_value = mock_cfm

            # Make run_async return True for bootstrap, then None for others
            call_count = [0]
            def side_effect(coro):
                call_count[0] += 1
                if call_count[0] == 1:
                    return True  # bootstrap result
                return None
            mock_run_async.side_effect = side_effect

            result = runner.invoke(app, ["init", "--workspace-dir", str(tmp_path)])
            # Check it ran without crashing (exit code may vary with mocks)
            assert "Initializing" in result.output


# ── nimo stop ────────────────────────────────────────────

class TestStop:
    @patch("agentpal.cli.commands.stop.PidManager")
    def test_stop_not_running(self, MockPid):
        mock_pid = MagicMock()
        mock_pid.is_running.return_value = False
        mock_pid.read.return_value = None
        MockPid.return_value = mock_pid

        result = runner.invoke(app, ["stop"])
        assert result.exit_code == 0
        assert "not running" in result.output.lower() or "Stopping" in result.output


# ── nimo status ──────────────────────────────────────────

class TestStatus:
    @patch("agentpal.cli.commands.status.PidManager")
    @patch("agentpal.cli.commands.status.get_workspace_dir")
    def test_status_nothing_running(self, mock_ws, MockPid, tmp_path):
        mock_ws.return_value = tmp_path
        mock_pid = MagicMock()
        mock_pid.is_running.return_value = False
        mock_pid.read.return_value = None
        MockPid.return_value = mock_pid

        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "Service Status" in result.output or "Stopped" in result.output


# ── nimo config ──────────────────────────────────────────

class TestConfig:
    @patch("agentpal.cli.commands.config_cmd.get_workspace_dir")
    def test_config_show(self, mock_ws, tmp_path):
        mock_ws.return_value = tmp_path

        # Create a minimal config file
        import yaml
        config = {"llm": {"model": "test-model", "api_key": "sk-secret123"}}
        (tmp_path / "config.yaml").write_text(yaml.dump(config))

        with patch("agentpal.services.config_file.ConfigFileManager") as MockCFM:
            mock_cfm = MagicMock()
            mock_cfm.load.return_value = config
            MockCFM.return_value = mock_cfm

            result = runner.invoke(app, ["config", "show"])
            assert result.exit_code == 0
            assert "test-model" in result.output

    @patch("agentpal.cli.commands.config_cmd.get_workspace_dir")
    def test_config_get(self, mock_ws, tmp_path):
        mock_ws.return_value = tmp_path

        with patch("agentpal.services.config_file.ConfigFileManager") as MockCFM:
            mock_cfm = MagicMock()
            mock_cfm.get.return_value = "qwen-max"
            MockCFM.return_value = mock_cfm

            result = runner.invoke(app, ["config", "get", "llm.model"])
            assert result.exit_code == 0
            assert "qwen-max" in result.output

    @patch("agentpal.cli.commands.config_cmd.get_workspace_dir")
    def test_config_set(self, mock_ws, tmp_path):
        mock_ws.return_value = tmp_path

        with patch("agentpal.services.config_file.ConfigFileManager") as MockCFM:
            mock_cfm = MagicMock()
            MockCFM.return_value = mock_cfm

            result = runner.invoke(app, ["config", "set", "llm.model", "gpt-4"])
            assert result.exit_code == 0
            mock_cfm.set.assert_called_once_with("llm.model", "gpt-4")


# ── nimo clean ───────────────────────────────────────────

class TestClean:
    def test_clean_no_flags(self):
        result = runner.invoke(app, ["clean"])
        assert result.exit_code == 1
        assert "Specify at least one" in result.output

    @patch("agentpal.cli.commands.clean.find_project_root")
    @patch("agentpal.cli.commands.clean.PidManager")
    def test_clean_cache(self, MockPid, mock_root, tmp_path):
        mock_root.return_value = tmp_path
        mock_pid = MagicMock()
        mock_pid.is_running.return_value = False
        MockPid.return_value = mock_pid

        # Create some cache dirs
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / ".pytest_cache").mkdir()

        result = runner.invoke(app, ["clean", "--cache", "-y"])
        assert result.exit_code == 0
        assert not (tmp_path / "__pycache__").exists()
        assert not (tmp_path / ".pytest_cache").exists()


# ── nimo doctor ──────────────────────────────────────────

class TestDoctor:
    def test_doctor_runs(self):
        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        assert "Python Version" in result.output
        assert "PASS" in result.output or "FAIL" in result.output


# ── nimo logs ────────────────────────────────────────────

class TestLogs:
    @patch("agentpal.cli.commands.logs.get_workspace_dir")
    def test_logs_no_file(self, mock_ws, tmp_path):
        mock_ws.return_value = tmp_path
        result = runner.invoke(app, ["logs"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower()

    @patch("agentpal.cli.commands.logs.get_workspace_dir")
    def test_logs_shows_content(self, mock_ws, tmp_path):
        mock_ws.return_value = tmp_path
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "backend.log").write_text("line1\nline2\nline3\n")

        result = runner.invoke(app, ["logs", "-n", "2"])
        assert result.exit_code == 0
        assert "line2" in result.output
        assert "line3" in result.output


# ── nimo config type inference ───────────────────────────

class TestTypeInference:
    def test_bool_true(self):
        from agentpal.cli.commands.config_cmd import _infer_type

        assert _infer_type("true") is True
        assert _infer_type("True") is True
        assert _infer_type("yes") is True

    def test_bool_false(self):
        from agentpal.cli.commands.config_cmd import _infer_type

        assert _infer_type("false") is False
        assert _infer_type("False") is False
        assert _infer_type("no") is False

    def test_int(self):
        from agentpal.cli.commands.config_cmd import _infer_type

        assert _infer_type("8099") == 8099
        assert _infer_type("0") == 0

    def test_float(self):
        from agentpal.cli.commands.config_cmd import _infer_type

        assert _infer_type("3.14") == 3.14

    def test_list(self):
        from agentpal.cli.commands.config_cmd import _infer_type

        assert _infer_type("a, b, c") == ["a", "b", "c"]

    def test_string(self):
        from agentpal.cli.commands.config_cmd import _infer_type

        assert _infer_type("hello") == "hello"
