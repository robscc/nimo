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

from agentpal.paths import get_nimo_home, get_plans_dir, get_skills_dir, get_workspace_dir


def _load_yaml_settings() -> dict[str, Any]:
    """从 ~/.nimo/config.yaml 加载配置（如果存在）。"""
    try:
        from agentpal.services.config_file import ConfigFileManager

        mgr = ConfigFileManager()
        if mgr.config_path.exists():
            result = mgr.to_settings_dict()
            import sys
            print(
                f"[config] YAML loaded from {mgr.config_path}: "
                f"dingtalk_app_key={result.get('dingtalk_app_key', '<MISSING>')!r}",
                file=sys.stderr,
            )
            return result
        else:
            import sys
            print(f"[config] YAML not found at {mgr.config_path}", file=sys.stderr)
    except Exception as e:
        import sys
        print(f"[config] YAML load FAILED: {e}", file=sys.stderr)
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
    llm_context_window: int = 128000  # 模型上下文窗口大小（token），0 = 禁用自动压缩

    # ── 记忆模块 ──────────────────────────────────────────
    # buffer: 纯内存滑动窗口
    # sqlite: 仅持久化
    # hybrid: buffer + sqlite（默认，推荐）
    # mem0:   mem0 语义记忆（需 pip install mem0ai）
    # reme:   ReMe 记忆管理（需 pip install reme-memory 或启动 ReMe server）
    memory_backend: Literal["buffer", "sqlite", "hybrid", "mem0", "reme", "reme_light"] = "reme_light"
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

    # ── ReMeLight 配置（memory_backend="reme_light" 时生效）──
    memory_reme_light_working_dir: str = ".reme"
    memory_reme_light_llm_api_key: str | None = None
    memory_reme_light_llm_base_url: str | None = None
    memory_reme_light_embedding_api_key: str | None = None
    memory_reme_light_embedding_base_url: str | None = None
    memory_reme_light_llm_model_config: dict[str, Any] | None = None
    memory_reme_light_embedding_model_config: dict[str, Any] | None = None
    memory_reme_light_vector_weight: float = 0.7
    memory_reme_light_candidate_multiplier: float = 3.0

    # ── Workspace ─────────────────────────────────────────
    # Agent 工作空间目录，默认 ~/.nimo（可通过 WORKSPACE_DIR 或 NIMO_HOME 环境变量覆盖）
    workspace_dir: str = str(get_workspace_dir())

    # ── Heartbeat ────────────────────────────────────────
    # 心跳机制：定期读取 HEARTBEAT.md 并执行其中的任务
    heartbeat_enabled: bool = True
    heartbeat_interval_minutes: int = 60  # 心跳间隔（分钟），默认 1 小时

    # ── Skill 系统 ──────────────────────────────────────
    # 技能安装目录，默认 ~/.nimo/skills_data（可通过 SKILLS_DIR 或 NIMO_HOME 环境变量覆盖）
    skills_dir: str = str(get_skills_dir())

    # ── Plan Mode ────────────────────────────────────────
    # 计划文件目录，默认 ~/.nimo/plans（可通过 PLANS_DIR 或 NIMO_HOME 环境变量覆盖）
    plans_dir: str = str(get_plans_dir())

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
    llm_debug: bool = False   # 打印每次 LLM 请求/响应的完整 JSON（不截断）

    # ── CORS ─────────────────────────────────────────────
    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # ── 异步任务结果注入 ─────────────────────────────────
    async_result_max_inject: int = 5      # system prompt 注入最近 N 条完成任务的完整结果
    async_result_max_chars: int = 500     # 每条结果最大字符数

    # ── ZMQ 消息总线（已废弃，使用 scheduler_* 系列配置）───
    zmq_router_addr: str = "ipc:///tmp/agentpal-router.sock"   # ROUTER socket 地址
    zmq_events_addr: str = "ipc:///tmp/agentpal-events.sock"   # PUB/SUB 事件 broker 地址
    zmq_pa_idle_timeout: int = 1800   # PA daemon 空闲回收超时（秒），默认 30 分钟
    zmq_sub_idle_timeout: int = 300   # SubAgent daemon 空闲回收超时（秒），默认 5 分钟

    # ── Scheduler（多进程 Agent 调度）──────────────────
    scheduler_router_addr: str = "ipc:///tmp/agentpal-router.sock"
    scheduler_events_addr: str = "ipc:///tmp/agentpal-events.sock"
    scheduler_pa_idle_timeout: int = 1800     # PA 空闲超时（秒），默认 30 分钟
    scheduler_sub_idle_timeout: int = 300     # SubAgent 空闲超时（秒），默认 5 分钟
    scheduler_health_check_interval: int = 30  # 健康检查间隔（秒）
    scheduler_process_start_timeout: int = 15  # 子进程启动超时（秒）
    scheduler_max_running_duration: int = 1800  # Agent RUNNING 最大持续时间（秒），超时强制 FAILED，默认 30 分钟

    @property
    def is_dev(self) -> bool:
        return self.app_env == "development"

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"


@lru_cache
def get_settings() -> Settings:
    return Settings()
