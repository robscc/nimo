"""ToolGuardManager — 工具调用安全卫士。

在工具执行前根据安全等级判断是否需要用户确认。

安全等级体系：
    Level 0: 毁灭性（rm -rf /, mkfs, dd if=, curl | bash）
    Level 1: 高危（rm, mv, shutdown, 通用 shell）
    Level 2: 中危（write_file, edit_file, execute_python_code）
    Level 3: 低危（browser_use, read_file）
    Level 4: 安全（get_current_time, skill_cli, cron_cli, send_file_to_user）

规则：tool_level < session_threshold → 需确认；tool_level >= session_threshold → 放行。
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from time import time
from typing import Any

from loguru import logger

# ── 数据结构 ──────────────────────────────────────────────


@dataclass
class ToolGuardRule:
    """参数级精细规则（正则匹配）。"""

    name: str
    tool: str  # 工具名
    pattern: str  # 正则表达式
    level: int  # 匹配到时的安全等级
    field: str = ""  # 匹配哪个参数字段（空 = JSON 序列化全部参数）

    _compiled: re.Pattern[str] | None = dc_field(default=None, repr=False, init=False)

    @property
    def compiled_pattern(self) -> re.Pattern[str]:
        if self._compiled is None:
            self._compiled = re.compile(self.pattern, re.IGNORECASE)
        return self._compiled


@dataclass
class GuardCheckResult:
    """check() 的返回结果。"""

    needs_confirmation: bool
    level: int
    rule_name: str | None  # 命中的规则名（None = 使用工具默认等级）
    tool_name: str
    description: str


@dataclass
class PendingGuardRequest:
    """等待用户确认的请求。"""

    event: asyncio.Event
    approved: bool = False
    created_at: float = dc_field(default_factory=time)
    tool_name: str = ""
    tool_input: dict[str, Any] = dc_field(default_factory=dict)


# ── 默认配置 ──────────────────────────────────────────────

DEFAULT_TOOL_GUARD_CONFIG: dict[str, Any] = {
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
}

# 安全等级描述
LEVEL_DESCRIPTIONS: dict[int, str] = {
    0: "毁灭性",
    1: "高危",
    2: "中危",
    3: "低危",
    4: "安全",
}


# ── ToolGuardManager ─────────────────────────────────────


class ToolGuardManager:
    """工具调用安全卫士（单例）。

    配置从 ~/.nimo/config.yaml 的 tool_guard 段读取，
    每次 check() 时通过 mtime 检测是否需要热加载。
    """

    _instance: ToolGuardManager | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.enabled: bool = True
        self.default_threshold: int = 2
        self.tool_levels: dict[str, int] = {}
        self.rules: list[ToolGuardRule] = []

        self._config_mtime: float = 0.0
        self._config_path: Path | None = None

        # pending 确认请求
        self._pending: dict[str, PendingGuardRequest] = {}
        self._pending_lock = threading.Lock()

        self._load_config()

    @classmethod
    def get_instance(cls) -> ToolGuardManager:
        """获取或创建单例实例。"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（测试用）。"""
        with cls._lock:
            cls._instance = None

    # ── 配置加载 ──────────────────────────────────────────

    def _load_config(self) -> None:
        """从 ConfigFileManager 读取 tool_guard 段。"""
        try:
            from agentpal.services.config_file import ConfigFileManager

            mgr = ConfigFileManager()
            self._config_path = mgr.config_path
            config = mgr.load()
            guard_config = config.get("tool_guard", {})

            if not guard_config:
                guard_config = DEFAULT_TOOL_GUARD_CONFIG

            self.enabled = guard_config.get("enabled", True)
            self.default_threshold = guard_config.get("default_threshold", 2)
            self.tool_levels = guard_config.get(
                "tool_levels", DEFAULT_TOOL_GUARD_CONFIG["tool_levels"]
            )

            # 解析规则
            self.rules = []
            for rule_dict in guard_config.get("rules", DEFAULT_TOOL_GUARD_CONFIG["rules"]):
                self.rules.append(
                    ToolGuardRule(
                        name=rule_dict.get("name", ""),
                        tool=rule_dict.get("tool", ""),
                        pattern=rule_dict.get("pattern", ""),
                        level=rule_dict.get("level", 0),
                        field=rule_dict.get("field", ""),
                    )
                )

            # 更新 mtime 缓存
            if self._config_path and self._config_path.exists():
                self._config_mtime = self._config_path.stat().st_mtime

            logger.debug(
                "ToolGuard config loaded: enabled={} threshold={} rules={}",
                self.enabled,
                self.default_threshold,
                len(self.rules),
            )
        except Exception as exc:
            logger.warning("ToolGuard config load failed, using defaults: {}", exc)
            self.enabled = DEFAULT_TOOL_GUARD_CONFIG["enabled"]
            self.default_threshold = DEFAULT_TOOL_GUARD_CONFIG["default_threshold"]
            self.tool_levels = DEFAULT_TOOL_GUARD_CONFIG["tool_levels"]
            self.rules = []

    def _maybe_reload(self) -> None:
        """mtime 检查 + reload（≤1s 粒度）。"""
        if self._config_path is None or not self._config_path.exists():
            return
        try:
            current_mtime = self._config_path.stat().st_mtime
            if current_mtime != self._config_mtime:
                logger.info("ToolGuard config file changed, reloading...")
                self._load_config()
        except Exception:
            pass

    # ── 安全等级判定 ──────────────────────────────────────

    def check(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        session_threshold: int | None = None,
    ) -> GuardCheckResult:
        """判断工具调用是否需要用户确认。

        Args:
            tool_name:         工具名称
            tool_input:        工具输入参数
            session_threshold: session 级阈值（None 回退到全局默认）

        Returns:
            GuardCheckResult，包含是否需要确认、安全等级、命中规则等
        """
        # 热加载检查
        self._maybe_reload()

        # Guard 未启用时直接放行
        if not self.enabled:
            return GuardCheckResult(
                needs_confirmation=False,
                level=4,
                rule_name=None,
                tool_name=tool_name,
                description="Guard disabled",
            )

        threshold = session_threshold if session_threshold is not None else self.default_threshold

        # 1. 先尝试匹配参数级规则（first-match wins）
        for rule in self.rules:
            if rule.tool != tool_name:
                continue

            # 确定要匹配的文本
            if rule.field:
                match_text = str(tool_input.get(rule.field, ""))
            else:
                match_text = json.dumps(tool_input, ensure_ascii=False)

            if rule.compiled_pattern.search(match_text):
                level = rule.level
                level_desc = LEVEL_DESCRIPTIONS.get(level, f"Level {level}")
                needs_confirm = level < threshold
                return GuardCheckResult(
                    needs_confirmation=needs_confirm,
                    level=level,
                    rule_name=rule.name,
                    tool_name=tool_name,
                    description=f"Rule '{rule.name}' matched: {level_desc} (level {level})",
                )

        # 2. 未命中规则，使用工具默认等级
        level = self.tool_levels.get(tool_name, 4)  # 未知工具默认安全
        level_desc = LEVEL_DESCRIPTIONS.get(level, f"Level {level}")
        needs_confirm = level < threshold
        return GuardCheckResult(
            needs_confirmation=needs_confirm,
            level=level,
            rule_name=None,
            tool_name=tool_name,
            description=f"Tool default level: {level_desc} (level {level})",
        )

    # ── Pending 请求管理 ──────────────────────────────────

    def create_pending(
        self,
        request_id: str,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> PendingGuardRequest:
        """创建一个等待用户确认的请求。"""
        pending = PendingGuardRequest(
            event=asyncio.Event(),
            tool_name=tool_name,
            tool_input=tool_input,
        )
        with self._pending_lock:
            self._pending[request_id] = pending
        return pending

    def resolve(self, request_id: str, approved: bool) -> bool:
        """解决一个 pending 请求。

        Returns:
            True 成功，False 未找到或已过期。
        """
        with self._pending_lock:
            pending = self._pending.get(request_id)
            if pending is None:
                return False

        pending.approved = approved
        pending.event.set()
        return True

    def get_pending(self, request_id: str) -> PendingGuardRequest | None:
        """获取 pending 请求。"""
        with self._pending_lock:
            return self._pending.get(request_id)

    def cleanup_expired(self, timeout: float = 300.0) -> int:
        """清理超时的 pending 请求。

        Args:
            timeout: 超时秒数（默认 5 分钟）

        Returns:
            清理的数量
        """
        now = time()
        expired_ids: list[str] = []
        with self._pending_lock:
            for rid, pending in self._pending.items():
                if now - pending.created_at > timeout:
                    expired_ids.append(rid)
            for rid in expired_ids:
                p = self._pending.pop(rid)
                # 让等待中的协程继续（标记为拒绝）
                if not p.event.is_set():
                    p.approved = False
                    p.event.set()
        if expired_ids:
            logger.info("ToolGuard: cleaned up {} expired pending requests", len(expired_ids))
        return len(expired_ids)

    def remove_pending(self, request_id: str) -> None:
        """移除一个 pending 请求（无论是否已解决）。"""
        with self._pending_lock:
            self._pending.pop(request_id, None)
