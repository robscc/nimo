"""应用全局配置。

优先级（从高到低）：
1. 环境变量
2. ~/.nimo/config.yaml
3. .env 文件
4. 代码默认值
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Tuple, Type

from pydantic_settings import BaseSettings, SettingsConfigDict


def _load_yaml_settings() -> dict[str, Any]:
    """从 ~/.nimo/config.yaml 加载配置（如果存在）。"""
    try:
        from agentpal.services.config_file import ConfigFileManager

        mgr = ConfigFileManager()
        if mgr.config_path.exists():
            return mgr.to_settings_dict()
    except Exception:
        pass
    return {}


class _YamlSettingsSource:
    """Pydantic settings source that reads from ~/.nimo/config.yaml."""

    def __init__(self, settings_cls: type) -> None:
        self._data = _load_yaml_settings()

    def __call__(self) -> dict[str, Any]:
        return self._data

    def __repr__(self) -> str:
        return "YamlSettingsSource(~/.nimo/config.yaml)"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> Tuple[Any, ...]:
        """自定义配置源优先级：init > env > yaml > .env > defaults。"""
        return (
            init_settings,
            env_settings,
            _YamlSettingsSource(settings_cls),
            dotenv_settings,
        )

    # ── 应用 ──────────────────────────────────────────────
    app_env: Literal["development", "production", "test"] = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8088
    app_secret_key: str = "change-me"

    # ── 数据库 ────────────────────────────────────────────
    # 仅使用 SQLite；异步驱动 aiosqlite
    database_url: str = "sqlite+aiosqlite:///./agentpal.db"

    # ── LLM ──────────────────────────────────────────────
    llm_provider: str = "dashscope"
    llm_model: str = "qwen-max"
    llm_api_key: str = ""
    llm_base_url: str = ""

    # ── 记忆模块 ──────────────────────────────────────────
    # buffer: 纯内存滑动窗口
    # sqlite: 仅持久化
    # hybrid: buffer + sqlite（默认，推荐）
    # mem0:   mem0 语义记忆（需 pip install mem0ai）
    # reme:   ReMe 记忆管理（需 pip install reme-memory 或启动 ReMe server）
    memory_backend: Literal["buffer", "sqlite", "hybrid", "mem0", "reme"] = "hybrid"
    memory_buffer_size: int = 30      # BufferMemory 最大条数
    memory_sqlite_limit: int = 200    # SQLite 每次查询上限

    # ── mem0 配置（memory_backend="mem0" 时生效）───────────
    memory_mem0_config: dict[str, Any] | None = None   # mem0 完整配置字典
    memory_mem0_infer: bool = False  # 是否启用 LLM 自动事实提取

    # ── ReMe 配置（memory_backend="reme" 时生效）──────────
    memory_reme_server_url: str | None = None   # ReMe 服务端 URL
    memory_reme_agent_name: str = "AgentPal"    # ReMe Agent 名称
    memory_reme_model_config: dict[str, Any] | None = None
    memory_reme_embedding_config: dict[str, Any] | None = None

    # ── Workspace ─────────────────────────────────────────
    # Agent 工作空间目录，默认 ~/.nimo（可通过 WORKSPACE_DIR 环境变量覆盖）
    workspace_dir: str = str(Path.home() / ".nimo")

    # ── Heartbeat ────────────────────────────────────────
    # 心跳机制：定期读取 HEARTBEAT.md 并执行其中的任务
    heartbeat_enabled: bool = True
    heartbeat_interval_minutes: int = 60  # 心跳间隔（分钟），默认 1 小时

    # ── Skill 系统 ──────────────────────────────────────
    skills_dir: str = "./skills_data"   # 技能安装目录

    # ── 渠道 ──────────────────────────────────────────────
    dingtalk_enabled: bool = False
    dingtalk_app_key: str = ""
    dingtalk_app_secret: str = ""
    dingtalk_robot_code: str = ""

    feishu_enabled: bool = False
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_verification_token: str = ""
    feishu_encrypt_key: str = ""

    imessage_enabled: bool = False

    # ── 日志 ──────────────────────────────────────────────
    log_level: str = "INFO"

    # ── CORS ─────────────────────────────────────────────
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"


@lru_cache
def get_settings() -> Settings:
    return Settings()
