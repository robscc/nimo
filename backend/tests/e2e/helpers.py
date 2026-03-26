"""E2E 测试共享工具模块。

提供 backend/frontend 健康检查、SSE 解析、API 客户端快捷方法、
以及 Playwright UI 操作封装。

所有 httpx 客户端均使用 trust_env=False 避免系统代理干扰。
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

import httpx
import pytest

# ── URLs ──────────────────────────────────────────────────

BACKEND_URL = "http://localhost:8099"
FRONTEND_URL = "http://localhost:3000"


# ── Health checks ─────────────────────────────────────────


def is_backend_ready() -> bool:
    """检查后端是否可访问（GET /health）。"""
    try:
        resp = httpx.get(f"{BACKEND_URL}/health", timeout=3.0, trust_env=False)
        return resp.is_success
    except Exception:
        return False


def is_frontend_ready() -> bool:
    """检查前端 dev server 是否可访问。"""
    try:
        resp = httpx.get(FRONTEND_URL, timeout=3.0, trust_env=False)
        return resp.status_code < 500
    except Exception:
        return False


# ── Pytest skip markers ───────────────────────────────────

require_backend = pytest.mark.skipif(
    not is_backend_ready(),
    reason="Backend not running at " + BACKEND_URL,
)

require_frontend = pytest.mark.skipif(
    not is_frontend_ready(),
    reason="Frontend not running at " + FRONTEND_URL,
)


# ── SSE parsing utilities ────────────────────────────────


def read_sse_events(response: httpx.Response) -> list[dict[str, Any]]:
    """解析 SSE text/event-stream 响应体，返回所有 JSON 事件。"""
    events: list[dict[str, Any]] = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            with contextlib.suppress(json.JSONDecodeError):
                events.append(json.loads(line[6:]))
    return events


def extract_reply_text(events: list[dict[str, Any]]) -> str:
    """从 SSE 事件列表中拼接所有 text_delta 为完整回复文本。"""
    parts: list[str] = []
    for e in events:
        if e.get("type") == "text_delta":
            parts.append(e.get("delta", ""))
    return "".join(parts)


def get_tool_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 SSE 事件列表中提取所有 tool_start 事件。"""
    return [e for e in events if e.get("type") == "tool_start"]


def assert_chat_completed(events: list[dict[str, Any]]) -> None:
    """断言 SSE 事件流正常完成（有 done，无 error）。"""
    types = [e.get("type") for e in events]
    assert "done" in types, f"未收到 done 事件，实际事件类型: {types}"
    errors = [e for e in events if e.get("type") == "error"]
    assert not errors, f"对话报错: {errors}"


# ── API client helpers ────────────────────────────────────


def create_session(
    client: httpx.Client,
    channel: str = "test",
) -> str:
    """通过 API 创建 session，返回 session_id。"""
    resp = client.post("/api/v1/sessions", params={"channel": channel})
    assert resp.is_success, f"创建 session 失败: {resp.status_code} {resp.text}"
    return resp.json()["id"]


def get_session_meta(client: httpx.Client, session_id: str) -> dict[str, Any]:
    """获取 session 元信息。"""
    resp = client.get(f"/api/v1/sessions/{session_id}/meta")
    assert resp.is_success, f"获取 session meta 失败: {resp.status_code} {resp.text}"
    return resp.json()


def update_session_config(
    client: httpx.Client,
    session_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """更新 session 配置，返回更新后的 meta。"""
    resp = client.patch(f"/api/v1/sessions/{session_id}/config", json=config)
    assert resp.is_success, f"更新 session config 失败: {resp.status_code} {resp.text}"
    return resp.json()


def send_chat(
    client: httpx.Client,
    session_id: str,
    message: str,
    *,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    """通过 API 发送聊天消息，返回 SSE 事件列表。

    注意：httpx.Client 必须用足够大的 timeout 初始化。
    """
    resp = client.post(
        "/api/v1/agent/chat",
        json={"session_id": session_id, "message": message},
        headers={"Accept": "text/event-stream"},
        timeout=timeout,
    )
    assert resp.is_success, f"chat 请求失败: {resp.status_code} {resp.text[:300]}"
    return read_sse_events(resp)


def get_sub_agent_mode(client: httpx.Client, session_id: str) -> str | None:
    """获取 session 的 sub_agent_mode，返回 null / "auto" / "manual" / "off"。"""
    meta = get_session_meta(client, session_id)
    return meta.get("sub_agent_mode")


def set_sub_agent_mode(
    client: httpx.Client,
    session_id: str,
    mode: str | None,
) -> dict[str, Any]:
    """设置 session 的 sub_agent_mode。"""
    return update_session_config(client, session_id, {"sub_agent_mode": mode})


# ── Playwright UI helpers ─────────────────────────────────


def open_meta_panel(page: Any) -> Any:
    """打开会话信息面板（若未打开），返回面板 locator。

    Args:
        page: Playwright Page 实例。

    Returns:
        面板的 Locator。
    """
    from playwright.sync_api import expect

    meta_panel = page.locator("[data-testid='session-meta-panel']")
    # 如果面板不可见，点击设置按钮打开
    if not meta_panel.is_visible():
        settings_btn = page.locator("button[title='会话信息']")
        expect(settings_btn).to_be_visible(timeout=5000)
        settings_btn.click()
        page.wait_for_timeout(500)
    expect(meta_panel).to_be_visible(timeout=5000)
    return meta_panel


def wait_for_chat_ready(page: Any, *, timeout: int = 3000) -> None:
    """等待 Chat 页面就绪（session 创建完成、输入框可见）。

    Args:
        page: Playwright Page 实例。
        timeout: 最大等待毫秒数。
    """
    from playwright.sync_api import expect

    page.goto(f"{FRONTEND_URL}/chat")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1500)  # 等待 session 创建
    input_box = page.locator("input[placeholder*='输入消息']")
    expect(input_box).to_be_visible(timeout=timeout)


def send_ui_message(page: Any, message: str) -> None:
    """通过 UI 发送一条消息。

    Args:
        page: Playwright Page 实例。
        message: 要发送的消息文本。
    """
    from playwright.sync_api import expect

    input_box = page.locator("input[placeholder*='输入消息']")
    expect(input_box).to_be_visible()
    input_box.fill(message)
    send_btn = page.locator("form button[type='submit']")
    expect(send_btn).to_be_enabled()
    send_btn.click()


def wait_for_assistant_reply(page: Any, *, timeout: int = 30000) -> str:
    """等待 AI 回复完成（流式结束），返回回复文本。

    Args:
        page: Playwright Page 实例。
        timeout: 最大等待毫秒数。

    Returns:
        AI 回复的内容文本。
    """
    page.wait_for_function(
        """() => {
            const bubbles = document.querySelectorAll('div.bg-white.border.rounded-2xl');
            const last = bubbles[bubbles.length - 1];
            return last && last.textContent.length > 2 && !last.querySelector('.animate-pulse');
        }""",
        timeout=timeout,
    )
    ai_bubble = page.locator("div.bg-white.border.rounded-2xl").last
    return ai_bubble.inner_text()
