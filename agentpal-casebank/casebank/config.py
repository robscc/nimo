"""Runtime configuration for the CaseBank sidecar."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class CollectorConfig(BaseModel):
    """Collection loop timing and resiliency settings."""

    poll_interval_seconds: int = 30
    reconnect_delay_seconds: int = 3
    request_timeout_seconds: int = 20


class BackfillConfig(BaseModel):
    """REST backfill behavior."""

    sessions_limit: int = 100
    tool_logs_limit: int = 200
    cron_limit: int = 200


class CaseBankConfig(BaseModel):
    """Top-level sidecar config."""

    base_url: str = "http://localhost:8099/api/v1"
    data_dir: Path = Field(default_factory=lambda: Path("./data"))
    collector: CollectorConfig = Field(default_factory=CollectorConfig)
    backfill: BackfillConfig = Field(default_factory=BackfillConfig)

    def ensure_data_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


def load_config(base_url: str | None = None, data_dir: str | None = None) -> CaseBankConfig:
    """Load config from arguments (MVP: no env/config file parsing yet)."""

    cfg = CaseBankConfig()
    if base_url:
        cfg.base_url = base_url.rstrip("/")
    if data_dir:
        cfg.data_dir = Path(data_dir)
    cfg.ensure_data_dir()
    return cfg
