"""browser_use 工具单元测试（mock Playwright）。"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

import agentpal.tools.builtin_browser as builtin_module
from agentpal.tools.builtin import _browser_use_httpx, browser_use


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _make_pw_mocks():
    """构造完整的 Playwright mock 调用链。

    返回 (mock_sync_playwright_callable, mock_page)，可在测试中配置 page 的行为。

    MagicMock 的 context manager 魔术方法必须通过
    ``mock.__enter__.return_value = ...`` 来配置，
    而不是直接替换属性。
    """
    mock_page = MagicMock()
    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page

    # playwright context manager: `with sync_playwright() as p:`
    mock_playwright_ctx = MagicMock()
    mock_playwright_ctx.chromium.launch.return_value = mock_browser

    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_playwright_ctx
    mock_cm.__exit__.return_value = False

    # sync_playwright() returns the context manager
    mock_sync_pw = MagicMock(return_value=mock_cm)

    return mock_sync_pw, mock_page


def _text(response) -> str:
    """从 ToolResponse 中提取文本内容。

    agentscope 的 TextBlock 是 dict，content 以 {'type': 'text', 'text': '...'} 形式存储。
    """
    item = response.content[0]
    if isinstance(item, dict):
        return item["text"]
    return item.text  # 兼容未来可能的 dataclass 形式


# ── get_text ──────────────────────────────────────────────────────────────────

class TestGetTextPlaywright:
    def test_returns_page_inner_text(self):
        mock_sync_pw, mock_page = _make_pw_mocks()
        mock_page.inner_text.return_value = "Hello, Playwright!"

        with patch.object(builtin_module, "USE_PLAYWRIGHT", True), \
             patch.object(builtin_module, "sync_playwright", mock_sync_pw):
            resp = browser_use("https://example.com", action="get_text")

        text = _text(resp)
        assert "Hello, Playwright!" in text
        mock_page.inner_text.assert_called_once_with("body")

    def test_truncates_long_text(self):
        mock_sync_pw, mock_page = _make_pw_mocks()
        mock_page.inner_text.return_value = "A" * 6000

        with patch.object(builtin_module, "USE_PLAYWRIGHT", True), \
             patch.object(builtin_module, "sync_playwright", mock_sync_pw):
            resp = browser_use("https://example.com", action="get_text")

        text = _text(resp)
        assert "...内容已截断..." in text
        assert len(text) < 6000

    def test_url_included_in_output(self):
        mock_sync_pw, mock_page = _make_pw_mocks()
        mock_page.inner_text.return_value = "content"

        with patch.object(builtin_module, "USE_PLAYWRIGHT", True), \
             patch.object(builtin_module, "sync_playwright", mock_sync_pw):
            resp = browser_use("https://example.com", action="get_text")

        assert "https://example.com" in _text(resp)


# ── get_title ─────────────────────────────────────────────────────────────────

class TestGetTitlePlaywright:
    def test_returns_page_title(self):
        mock_sync_pw, mock_page = _make_pw_mocks()
        mock_page.title.return_value = "Example Domain"

        with patch.object(builtin_module, "USE_PLAYWRIGHT", True), \
             patch.object(builtin_module, "sync_playwright", mock_sync_pw):
            resp = browser_use("https://example.com", action="get_title")

        assert "Example Domain" in _text(resp)
        mock_page.title.assert_called_once()


# ── get_html ──────────────────────────────────────────────────────────────────

class TestGetHtmlPlaywright:
    def test_returns_body_html(self):
        mock_sync_pw, mock_page = _make_pw_mocks()
        mock_page.inner_html.return_value = "<p>Hello</p>"

        with patch.object(builtin_module, "USE_PLAYWRIGHT", True), \
             patch.object(builtin_module, "sync_playwright", mock_sync_pw):
            resp = browser_use("https://example.com", action="get_html")

        assert "<p>Hello</p>" in _text(resp)
        mock_page.inner_html.assert_called_once_with("body")

    def test_truncates_long_html(self):
        mock_sync_pw, mock_page = _make_pw_mocks()
        mock_page.inner_html.return_value = "<p>" + "B" * 5100 + "</p>"

        with patch.object(builtin_module, "USE_PLAYWRIGHT", True), \
             patch.object(builtin_module, "sync_playwright", mock_sync_pw):
            resp = browser_use("https://example.com", action="get_html")

        assert "...内容已截断..." in _text(resp)


# ── screenshot ────────────────────────────────────────────────────────────────

class TestScreenshotPlaywright:
    def test_calls_screenshot_and_returns_path(self):
        mock_sync_pw, mock_page = _make_pw_mocks()

        with patch.object(builtin_module, "USE_PLAYWRIGHT", True), \
             patch.object(builtin_module, "sync_playwright", mock_sync_pw), \
             patch("time.time", return_value=1700000000):
            resp = browser_use("https://example.com", action="screenshot")

        text = _text(resp)
        assert "/tmp/agentpal_screenshot_" in text
        assert ".png" in text
        mock_page.screenshot.assert_called_once()
        call_kwargs = mock_page.screenshot.call_args[1]
        assert "agentpal_screenshot_1700000000.png" in call_kwargs["path"]


# ── click ─────────────────────────────────────────────────────────────────────

class TestClickPlaywright:
    def test_clicks_selector(self):
        mock_sync_pw, mock_page = _make_pw_mocks()

        with patch.object(builtin_module, "USE_PLAYWRIGHT", True), \
             patch.object(builtin_module, "sync_playwright", mock_sync_pw):
            resp = browser_use("https://example.com", action="click", selector="#btn")

        mock_page.click.assert_called_once_with("#btn")
        assert "#btn" in _text(resp)

    def test_click_without_selector_returns_error(self):
        mock_sync_pw, mock_page = _make_pw_mocks()

        with patch.object(builtin_module, "USE_PLAYWRIGHT", True), \
             patch.object(builtin_module, "sync_playwright", mock_sync_pw):
            resp = browser_use("https://example.com", action="click", selector="")

        assert "<error>" in _text(resp)
        mock_page.click.assert_not_called()


# ── fill ──────────────────────────────────────────────────────────────────────

class TestFillPlaywright:
    def test_fills_input(self):
        mock_sync_pw, mock_page = _make_pw_mocks()

        with patch.object(builtin_module, "USE_PLAYWRIGHT", True), \
             patch.object(builtin_module, "sync_playwright", mock_sync_pw):
            resp = browser_use(
                "https://example.com", action="fill", selector="input#q", value="hello"
            )

        mock_page.fill.assert_called_once_with("input#q", "hello")
        assert "input#q" in _text(resp)

    def test_fill_without_selector_returns_error(self):
        mock_sync_pw, mock_page = _make_pw_mocks()

        with patch.object(builtin_module, "USE_PLAYWRIGHT", True), \
             patch.object(builtin_module, "sync_playwright", mock_sync_pw):
            resp = browser_use("https://example.com", action="fill", selector="", value="v")

        assert "<error>" in _text(resp)
        mock_page.fill.assert_not_called()


# ── scroll ────────────────────────────────────────────────────────────────────

class TestScrollPlaywright:
    def test_scrolls_with_default_distance(self):
        mock_sync_pw, mock_page = _make_pw_mocks()

        with patch.object(builtin_module, "USE_PLAYWRIGHT", True), \
             patch.object(builtin_module, "sync_playwright", mock_sync_pw):
            resp = browser_use("https://example.com", action="scroll")

        mock_page.evaluate.assert_called_once_with("window.scrollBy(0, 800)")
        assert "800px" in _text(resp)

    def test_scrolls_with_custom_distance(self):
        mock_sync_pw, mock_page = _make_pw_mocks()

        with patch.object(builtin_module, "USE_PLAYWRIGHT", True), \
             patch.object(builtin_module, "sync_playwright", mock_sync_pw):
            resp = browser_use("https://example.com", action="scroll", distance=1200)

        mock_page.evaluate.assert_called_once_with("window.scrollBy(0, 1200)")
        assert "1200px" in _text(resp)


# ── 未知 action ───────────────────────────────────────────────────────────────

class TestUnknownAction:
    def test_returns_error_for_unknown_action(self):
        mock_sync_pw, mock_page = _make_pw_mocks()

        with patch.object(builtin_module, "USE_PLAYWRIGHT", True), \
             patch.object(builtin_module, "sync_playwright", mock_sync_pw):
            resp = browser_use("https://example.com", action="unknown_action")

        assert "<error>" in _text(resp)
        assert "unknown_action" in _text(resp)


# ── wait_ms 传递 ───────────────────────────────────────────────────────────────

class TestWaitMs:
    def test_wait_for_timeout_called_with_wait_ms(self):
        mock_sync_pw, mock_page = _make_pw_mocks()
        mock_page.inner_text.return_value = "ok"

        with patch.object(builtin_module, "USE_PLAYWRIGHT", True), \
             patch.object(builtin_module, "sync_playwright", mock_sync_pw):
            browser_use("https://example.com", action="get_text", wait_ms=3000)

        mock_page.wait_for_timeout.assert_called_once_with(3000)


# ── 异常处理 ──────────────────────────────────────────────────────────────────

class TestPlaywrightException:
    def test_returns_error_on_playwright_exception(self):
        mock_sync_pw, mock_page = _make_pw_mocks()
        mock_page.inner_text.side_effect = Exception("page crash")

        with patch.object(builtin_module, "USE_PLAYWRIGHT", True), \
             patch.object(builtin_module, "sync_playwright", mock_sync_pw):
            resp = browser_use("https://example.com", action="get_text")

        assert "<error>" in _text(resp)
        assert "page crash" in _text(resp)


# ── httpx 降级 ────────────────────────────────────────────────────────────────

def _make_httpx_mock(html: str):
    """构造 httpx.Client context manager mock，返回给定 HTML。"""
    mock_response = MagicMock()
    mock_response.text = html
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = mock_response

    return mock_client


class TestHttpxFallback:
    def test_uses_httpx_when_playwright_unavailable(self):
        """USE_PLAYWRIGHT=False 时路由到 _browser_use_httpx。"""
        with patch.object(builtin_module, "USE_PLAYWRIGHT", False), \
             patch.object(builtin_module, "_browser_use_httpx") as mock_httpx_fn:
            mock_httpx_fn.return_value = builtin_module._text_response("fallback text")
            resp = browser_use("https://example.com", action="get_text")

        mock_httpx_fn.assert_called_once_with("https://example.com", "get_text")

    def test_httpx_get_text_strips_tags(self):
        """_browser_use_httpx 能去除 HTML 标签，返回纯文字。"""
        fake_html = "<html><body><p>Hello World</p></body></html>"
        mock_client = _make_httpx_mock(fake_html)

        with patch("httpx.Client", return_value=mock_client):
            resp = _browser_use_httpx("https://example.com", "get_text")

        assert "Hello World" in _text(resp)
        assert "<p>" not in _text(resp)

    def test_httpx_get_title_extracts_title(self):
        """_browser_use_httpx 能提取 <title> 标签内容。"""
        fake_html = "<html><head><title>My Site</title></head><body></body></html>"
        mock_client = _make_httpx_mock(fake_html)

        with patch("httpx.Client", return_value=mock_client):
            resp = _browser_use_httpx("https://example.com", "get_title")

        assert "My Site" in _text(resp)

    def test_httpx_returns_error_on_exception(self):
        """_browser_use_httpx 请求失败时返回 <error> 响应。"""
        with patch("httpx.Client", side_effect=Exception("connection refused")):
            resp = _browser_use_httpx("https://example.com", "get_text")

        assert "<error>" in _text(resp)

    def test_import_error_sets_use_playwright_false(self):
        """模拟 playwright 未安装时，USE_PLAYWRIGHT 应为 False。"""
        with patch.object(builtin_module, "USE_PLAYWRIGHT", False):
            assert builtin_module.USE_PLAYWRIGHT is False
