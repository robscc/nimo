"""httpx API 测试：Session 级 SubAgent 模式配置 CRUD。

纯后端 API 测试，不需要前端运行。
通过 httpx 直接调用 REST API 验证：
  - Session meta 返回 sub_agent_mode
  - PATCH config 设置 sub_agent_mode（auto / manual / off / null）
  - 无效值返回 400
  - 多 session 之间配置隔离
  - 其他字段（enabled_tools 等）不被影响

运行方式：
  cd backend && .venv/bin/pytest tests/e2e/test_sub_agent_config_api.py -v --tb=short

依赖：后端运行在 http://localhost:8099。
"""

from __future__ import annotations

import httpx
import pytest

from tests.e2e.helpers import (
    create_session,
    get_session_meta,
    get_sub_agent_mode,
    require_backend,
    set_sub_agent_mode,
    update_session_config,
)

pytestmark = [pytest.mark.e2e, require_backend]


class TestSubAgentModeAPI:
    """Session sub_agent_mode CRUD API 测试。"""

    def test_new_session_mode_is_null(self, api_client: httpx.Client, session_id: str):
        """新创建的 session 的 sub_agent_mode 应为 null（跟随全局）。"""
        mode = get_sub_agent_mode(api_client, session_id)
        assert mode is None, f"新 session mode 应为 null，实际: {mode}"

    def test_set_mode_auto(self, api_client: httpx.Client, session_id: str):
        """可以将 sub_agent_mode 设为 auto。"""
        meta = set_sub_agent_mode(api_client, session_id, "auto")
        assert meta["sub_agent_mode"] == "auto"

        # 再次读取验证持久化
        mode = get_sub_agent_mode(api_client, session_id)
        assert mode == "auto"

    def test_set_mode_manual(self, api_client: httpx.Client, session_id: str):
        """可以将 sub_agent_mode 设为 manual。"""
        meta = set_sub_agent_mode(api_client, session_id, "manual")
        assert meta["sub_agent_mode"] == "manual"

        mode = get_sub_agent_mode(api_client, session_id)
        assert mode == "manual"

    def test_set_mode_off(self, api_client: httpx.Client, session_id: str):
        """可以将 sub_agent_mode 设为 off。"""
        meta = set_sub_agent_mode(api_client, session_id, "off")
        assert meta["sub_agent_mode"] == "off"

        mode = get_sub_agent_mode(api_client, session_id)
        assert mode == "off"

    def test_reset_mode_to_null(self, api_client: httpx.Client, session_id: str):
        """可以将 sub_agent_mode 重置为 null（跟随全局）。"""
        # 先设为 auto
        set_sub_agent_mode(api_client, session_id, "auto")
        assert get_sub_agent_mode(api_client, session_id) == "auto"

        # 重置为 null
        meta = set_sub_agent_mode(api_client, session_id, None)
        assert meta["sub_agent_mode"] is None

        mode = get_sub_agent_mode(api_client, session_id)
        assert mode is None

    def test_invalid_mode_returns_400(self, api_client: httpx.Client, session_id: str):
        """设置无效的 sub_agent_mode 值应返回 400。"""
        resp = api_client.patch(
            f"/api/v1/sessions/{session_id}/config",
            json={"sub_agent_mode": "invalid_value"},
        )
        assert resp.status_code == 400, f"应返回 400，实际: {resp.status_code}"
        assert "invalid" in resp.json().get("detail", "").lower() or \
               "Invalid" in resp.json().get("detail", "")

    def test_mode_cycle_auto_manual_off_null(self, api_client: httpx.Client, session_id: str):
        """完整的模式循环切换：auto → manual → off → null。"""
        # auto
        set_sub_agent_mode(api_client, session_id, "auto")
        assert get_sub_agent_mode(api_client, session_id) == "auto"

        # manual
        set_sub_agent_mode(api_client, session_id, "manual")
        assert get_sub_agent_mode(api_client, session_id) == "manual"

        # off
        set_sub_agent_mode(api_client, session_id, "off")
        assert get_sub_agent_mode(api_client, session_id) == "off"

        # null
        set_sub_agent_mode(api_client, session_id, None)
        assert get_sub_agent_mode(api_client, session_id) is None


class TestSubAgentModeIsolation:
    """多 session 之间的 sub_agent_mode 配置隔离。"""

    def test_mode_isolated_between_sessions(self, api_client: httpx.Client):
        """不同 session 的 sub_agent_mode 应互相独立。"""
        session_a = create_session(api_client, channel="test")
        session_b = create_session(api_client, channel="test")

        # session_a → auto
        set_sub_agent_mode(api_client, session_a, "auto")

        # session_b → off
        set_sub_agent_mode(api_client, session_b, "off")

        # 验证互不影响
        assert get_sub_agent_mode(api_client, session_a) == "auto"
        assert get_sub_agent_mode(api_client, session_b) == "off"

    def test_mode_change_does_not_affect_other_fields(self, api_client: httpx.Client):
        """更新 sub_agent_mode 不应影响 enabled_tools 等其他字段。"""
        session_id = create_session(api_client, channel="test")

        # 先设置 enabled_tools
        update_session_config(
            api_client,
            session_id,
            {"enabled_tools": ["read_file", "get_current_time"]},
        )
        meta_before = get_session_meta(api_client, session_id)
        tools_before = meta_before["enabled_tools"]

        # 然后设置 sub_agent_mode
        set_sub_agent_mode(api_client, session_id, "auto")

        # enabled_tools 应不变
        meta_after = get_session_meta(api_client, session_id)
        assert meta_after["enabled_tools"] == tools_before, (
            f"enabled_tools 不应被影响: before={tools_before}, after={meta_after['enabled_tools']}"
        )
        assert meta_after["sub_agent_mode"] == "auto"


class TestSessionConfigAPIGeneral:
    """Session config API 通用测试。"""

    def test_get_meta_returns_all_fields(self, api_client: httpx.Client, session_id: str):
        """GET /sessions/{id}/meta 应返回完整的元信息字段。"""
        meta = get_session_meta(api_client, session_id)

        # 必须字段
        assert "id" in meta
        assert "channel" in meta
        assert "model_name" in meta
        assert "enabled_tools" in meta
        assert "enabled_skills" in meta
        assert "sub_agent_mode" in meta
        assert "message_count" in meta
        assert "created_at" in meta
        assert "updated_at" in meta

    def test_update_config_returns_updated_meta(self, api_client: httpx.Client, session_id: str):
        """PATCH /sessions/{id}/config 应返回更新后的完整 meta。"""
        meta = update_session_config(
            api_client,
            session_id,
            {"sub_agent_mode": "manual"},
        )

        assert meta["id"] == session_id
        assert meta["sub_agent_mode"] == "manual"
        assert "model_name" in meta
        assert "message_count" in meta

    def test_nonexistent_session_returns_404(self, api_client: httpx.Client):
        """对不存在的 session 操作应返回 404。"""
        fake_id = "test:nonexistent-session-12345"

        # GET meta
        resp = api_client.get(f"/api/v1/sessions/{fake_id}/meta")
        assert resp.status_code == 404

        # PATCH config
        resp = api_client.patch(
            f"/api/v1/sessions/{fake_id}/config",
            json={"sub_agent_mode": "auto"},
        )
        assert resp.status_code == 404

    def test_update_enabled_tools(self, api_client: httpx.Client, session_id: str):
        """可以通过 PATCH config 更新 enabled_tools。"""
        tools = ["read_file", "get_current_time"]
        meta = update_session_config(
            api_client,
            session_id,
            {"enabled_tools": tools},
        )
        assert meta["enabled_tools"] == tools

        # 验证持久化
        meta2 = get_session_meta(api_client, session_id)
        assert meta2["enabled_tools"] == tools

    def test_reset_enabled_tools_to_null(self, api_client: httpx.Client, session_id: str):
        """可以将 enabled_tools 重置为 null（跟随全局）。"""
        # 先设值
        update_session_config(
            api_client,
            session_id,
            {"enabled_tools": ["read_file"]},
        )

        # 重置为 null
        meta = update_session_config(
            api_client,
            session_id,
            {"enabled_tools": None},
        )
        assert meta["enabled_tools"] is None

    def test_update_multiple_fields_at_once(self, api_client: httpx.Client, session_id: str):
        """可以一次更新多个配置字段。"""
        meta = update_session_config(
            api_client,
            session_id,
            {
                "sub_agent_mode": "auto",
                "enabled_tools": ["read_file"],
                "tool_guard_threshold": 3,
            },
        )
        assert meta["sub_agent_mode"] == "auto"
        assert meta["enabled_tools"] == ["read_file"]
        assert meta["tool_guard_threshold"] == 3
