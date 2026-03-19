"""ConfigFileManager — 管理 ~/.nimo/config.yaml 配置文件。

将服务配置持久化到 ~/.nimo/config.yaml，替代 .env，增强可读性。
支持读取、写入、合并更新，以及与 pydantic Settings 的双向同步。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from loguru import logger

# ~/.nimo/config.yaml 的默认模板
DEFAULT_CONFIG: dict[str, Any] = {
    "app": {
        "env": "development",
        "host": "0.0.0.0",
        "port": 8099,
        "secret_key": "change-me",
    },
    "llm": {
        "provider": "dashscope",
        "model": "qwen-max",
        "api_key": "",
        "base_url": "",
        "context_window": 128000,
    },
    "database": {
        "url": "sqlite+aiosqlite:///./agentpal.db",
    },
    "memory": {
        "backend": "hybrid",
        "buffer_size": 30,
        "sqlite_limit": 200,
    },
    "skills": {
        "dir": "./skills_data",
    },
    "channels": {
        "dingtalk": {"enabled": False, "app_key": "", "app_secret": "", "robot_code": ""},
        "feishu": {
            "enabled": False,
            "app_id": "",
            "app_secret": "",
            "verification_token": "",
            "encrypt_key": "",
        },
        "imessage": {"enabled": False},
    },
    "log": {
        "level": "INFO",
    },
    "cors": {
        "origins": ["http://localhost:3000", "http://localhost:5173"],
    },
    "tool_guard": {
        "enabled": True,
        "default_threshold": 2,
        "tool_levels": {
            "execute_shell_command": 1,
            "write_file": 2,
            "edit_file": 2,
            "execute_python_code": 1,
            "browser_use": 3,
            "read_file": 4,
            "get_current_time": 4,
            "skill_cli": 4,
            "cron_cli": 4,
            "send_file_to_user": 4,
        },
        "rules": [
            {
                "name": "destructive_fs",
                "tool": "execute_shell_command",
                "pattern": r"(rm\s+-rf|rmdir|mkfs|dd\s+if=|format\s+)",
                "level": 0,
            },
            {
                "name": "remote_code_exec",
                "tool": "execute_shell_command",
                "pattern": r"(curl.*\|\s*(sh|bash)|wget.*\|\s*(sh|bash))",
                "level": 0,
            },
            {
                "name": "system_control",
                "tool": "execute_shell_command",
                "pattern": r"(shutdown|reboot|init\s+[0-6]|systemctl\s+(stop|disable))",
                "level": 0,
            },
            {
                "name": "file_delete_move",
                "tool": "execute_shell_command",
                "pattern": r"(\brm\b|\bmv\b|\bshred\b)",
                "level": 1,
            },
            {
                "name": "write_system_path",
                "tool": "write_file",
                "field": "file_path",
                "pattern": r"^/(etc|usr|bin|sbin|boot)/",
                "level": 0,
            },
        ],
    },
}

# YAML 到 Settings 字段的映射（YAML 嵌套路径 → Settings 属性名）
_YAML_TO_SETTINGS: dict[str, str] = {
    "app.env": "app_env",
    "app.host": "app_host",
    "app.port": "app_port",
    "app.secret_key": "app_secret_key",
    "llm.provider": "llm_provider",
    "llm.model": "llm_model",
    "llm.api_key": "llm_api_key",
    "llm.base_url": "llm_base_url",
    "llm.context_window": "llm_context_window",
    "database.url": "database_url",
    "memory.backend": "memory_backend",
    "memory.buffer_size": "memory_buffer_size",
    "memory.sqlite_limit": "memory_sqlite_limit",
    "skills.dir": "skills_dir",
    "channels.dingtalk.enabled": "dingtalk_enabled",
    "channels.dingtalk.app_key": "dingtalk_app_key",
    "channels.dingtalk.app_secret": "dingtalk_app_secret",
    "channels.dingtalk.robot_code": "dingtalk_robot_code",
    "channels.feishu.enabled": "feishu_enabled",
    "channels.feishu.app_id": "feishu_app_id",
    "channels.feishu.app_secret": "feishu_app_secret",
    "channels.feishu.verification_token": "feishu_verification_token",
    "channels.feishu.encrypt_key": "feishu_encrypt_key",
    "channels.imessage.enabled": "imessage_enabled",
    "log.level": "log_level",
    "cors.origins": "cors_origins",
}


class ConfigFileManager:
    """管理 ~/.nimo/config.yaml 的读写。"""

    CONFIG_FILENAME = "config.yaml"

    def __init__(self, nimo_dir: Path | str | None = None) -> None:
        self.nimo_dir = Path(nimo_dir) if nimo_dir else Path.home() / ".nimo"
        self.config_path = self.nimo_dir / self.CONFIG_FILENAME

    # ── 读取 ──────────────────────────────────────────────

    def load(self) -> dict[str, Any]:
        """读取 config.yaml，不存在则返回默认配置。"""
        if not self.config_path.exists():
            return _deep_copy(DEFAULT_CONFIG)
        try:
            text = self.config_path.read_text(encoding="utf-8")
            data = yaml.safe_load(text)
            if not isinstance(data, dict):
                logger.warning(f"config.yaml 格式错误，使用默认配置")
                return _deep_copy(DEFAULT_CONFIG)
            return _deep_merge(DEFAULT_CONFIG, data)
        except Exception as e:
            logger.error(f"读取 config.yaml 失败: {e}，使用默认配置")
            return _deep_copy(DEFAULT_CONFIG)

    # ── 写入 ──────────────────────────────────────────────

    def save(self, config: dict[str, Any]) -> None:
        """将配置写入 config.yaml。"""
        self.nimo_dir.mkdir(parents=True, exist_ok=True)
        text = yaml.dump(
            config,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
        self.config_path.write_text(text, encoding="utf-8")
        logger.info(f"配置已保存到 {self.config_path}")

    def save_defaults(self) -> bool:
        """如果 config.yaml 不存在，写入默认模板。

        Returns:
            True 表示写入了默认配置，False 表示已存在跳过。
        """
        if self.config_path.exists():
            return False
        self.save(_deep_copy(DEFAULT_CONFIG))
        return True

    # ── 更新 ──────────────────────────────────────────────

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        """合并更新配置并写回文件。

        Args:
            patch: 要合并的配置片段（嵌套 dict）

        Returns:
            更新后的完整配置
        """
        current = self.load()
        merged = _deep_merge(current, patch)
        self.save(merged)
        return merged

    # ── 获取指定路径的值 ──────────────────────────────────

    def get(self, dotted_path: str, default: Any = None) -> Any:
        """通过点号分隔路径获取值，如 'llm.model'。"""
        config = self.load()
        return _get_nested(config, dotted_path, default)

    def set(self, dotted_path: str, value: Any) -> dict[str, Any]:
        """通过点号分隔路径设置值，如 'llm.model'。"""
        config = self.load()
        _set_nested(config, dotted_path, value)
        self.save(config)
        return config

    # ── 转换为 Settings 兼容的 flat dict ──────────────────

    def to_settings_dict(self) -> dict[str, Any]:
        """将 YAML 配置展平为 Settings 可用的字段映射。"""
        config = self.load()
        result: dict[str, Any] = {}
        for yaml_path, settings_key in _YAML_TO_SETTINGS.items():
            val = _get_nested(config, yaml_path)
            if val is not None:
                result[settings_key] = val
        return result

    # ── 从 Settings 反向生成 YAML 配置 ────────────────────

    @staticmethod
    def from_settings_dict(settings_dict: dict[str, Any]) -> dict[str, Any]:
        """将 Settings flat dict 转换回 YAML 嵌套结构。"""
        config = _deep_copy(DEFAULT_CONFIG)
        reverse_map = {v: k for k, v in _YAML_TO_SETTINGS.items()}
        for key, value in settings_dict.items():
            yaml_path = reverse_map.get(key)
            if yaml_path:
                _set_nested(config, yaml_path, value)
        return config


# ── 辅助函数 ──────────────────────────────────────────────


def _deep_copy(d: dict[str, Any]) -> dict[str, Any]:
    """简单的 dict 深拷贝（不含复杂对象）。"""
    import copy
    return copy.deepcopy(d)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并两个 dict，override 优先。"""
    result = _deep_copy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _get_nested(d: dict[str, Any], path: str, default: Any = None) -> Any:
    """通过 'a.b.c' 路径获取嵌套 dict 的值。"""
    keys = path.split(".")
    current = d
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return default
    return current


def _set_nested(d: dict[str, Any], path: str, value: Any) -> None:
    """通过 'a.b.c' 路径设置嵌套 dict 的值。"""
    keys = path.split(".")
    current = d
    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value
