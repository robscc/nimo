"""应用全局配置，通过环境变量 / .env 文件加载。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
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
    memory_backend: Literal["buffer", "sqlite", "hybrid"] = "hybrid"
    memory_buffer_size: int = 30      # BufferMemory 最大条数
    memory_sqlite_limit: int = 200    # SQLite 每次查询上限

    # ── Workspace ─────────────────────────────────────────
    # Agent 工作空间目录，默认 ~/.nimo（可通过 WORKSPACE_DIR 环境变量覆盖）
    workspace_dir: str = str(Path.home() / ".nimo")

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
