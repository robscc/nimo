"""ConfigFileManager 单元测试。"""

from __future__ import annotations

import pytest
from pathlib import Path

from agentpal.services.config_file import ConfigFileManager, DEFAULT_CONFIG


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """返回一个临时目录作为 ~/.nimo 目录。"""
    return tmp_path / ".nimo"


@pytest.fixture
def mgr(config_dir: Path) -> ConfigFileManager:
    return ConfigFileManager(config_dir)


class TestConfigFileManager:
    """ConfigFileManager 基本读写功能。"""

    def test_load_returns_defaults_when_no_file(self, mgr: ConfigFileManager):
        """无 config.yaml 时返回默认配置。"""
        config = mgr.load()
        assert config["app"]["port"] == 8099
        assert config["llm"]["provider"] == "dashscope"
        assert config["memory"]["backend"] == "hybrid"

    def test_save_defaults_creates_file(self, mgr: ConfigFileManager):
        """save_defaults 创建 config.yaml。"""
        assert not mgr.config_path.exists()
        result = mgr.save_defaults()
        assert result is True
        assert mgr.config_path.exists()
        # 再次调用应幂等
        assert mgr.save_defaults() is False

    def test_save_and_load_roundtrip(self, mgr: ConfigFileManager):
        """save 后 load 结果一致。"""
        config = {"app": {"port": 9999}, "llm": {"model": "gpt-4o"}}
        mgr.save(config)
        loaded = mgr.load()
        assert loaded["app"]["port"] == 9999
        assert loaded["llm"]["model"] == "gpt-4o"
        # 默认值应仍存在
        assert loaded["llm"]["provider"] == "dashscope"

    def test_update_merges_config(self, mgr: ConfigFileManager):
        """update 合并更新而非覆盖。"""
        mgr.save_defaults()
        mgr.update({"llm": {"model": "gpt-4o-mini"}})
        config = mgr.load()
        assert config["llm"]["model"] == "gpt-4o-mini"
        # 其他字段保留
        assert config["llm"]["provider"] == "dashscope"
        assert config["app"]["port"] == 8099

    def test_get_nested_value(self, mgr: ConfigFileManager):
        """通过点号路径获取嵌套值。"""
        mgr.save_defaults()
        assert mgr.get("llm.provider") == "dashscope"
        assert mgr.get("app.port") == 8099
        assert mgr.get("channels.dingtalk.enabled") is False
        assert mgr.get("nonexistent.key", "fallback") == "fallback"

    def test_set_nested_value(self, mgr: ConfigFileManager):
        """通过点号路径设置嵌套值。"""
        mgr.save_defaults()
        mgr.set("llm.model", "claude-3.5-sonnet")
        assert mgr.get("llm.model") == "claude-3.5-sonnet"
        # 其他值不受影响
        assert mgr.get("llm.provider") == "dashscope"


class TestConfigToSettings:
    """config.yaml ↔ Settings 转换。"""

    def test_to_settings_dict(self, mgr: ConfigFileManager):
        """YAML 配置转换为 Settings flat dict。"""
        mgr.save_defaults()
        mgr.set("llm.model", "gpt-4o")
        settings_dict = mgr.to_settings_dict()
        assert settings_dict["llm_model"] == "gpt-4o"
        assert settings_dict["llm_provider"] == "dashscope"
        assert settings_dict["memory_backend"] == "hybrid"

    def test_from_settings_dict(self):
        """Settings flat dict 转换回 YAML 嵌套结构。"""
        flat = {
            "llm_model": "gpt-4o",
            "llm_provider": "openai",
            "app_port": 9000,
        }
        config = ConfigFileManager.from_settings_dict(flat)
        assert config["llm"]["model"] == "gpt-4o"
        assert config["llm"]["provider"] == "openai"
        assert config["app"]["port"] == 9000


class TestConfigYAMLFormat:
    """确保 YAML 文件格式正确可读。"""

    def test_yaml_is_human_readable(self, mgr: ConfigFileManager):
        """生成的 YAML 是人类可读的（非 flow style）。"""
        mgr.save_defaults()
        content = mgr.config_path.read_text(encoding="utf-8")
        # 应该是多行格式，不是 {} 压缩格式
        assert "app:" in content
        assert "llm:" in content
        assert "{" not in content.split("\n")[0]  # 第一行不应是 flow style

    def test_malformed_yaml_falls_back(self, mgr: ConfigFileManager):
        """YAML 格式错误时回退到默认配置。"""
        mgr.nimo_dir.mkdir(parents=True, exist_ok=True)
        mgr.config_path.write_text("not: [valid: yaml: {bad", encoding="utf-8")
        config = mgr.load()
        # 应回退到默认
        assert "app" in config
