"""Playwright UI 测试：SessionMetaPanel SubAgent 调度模式。

测试前端 ChatPage 中的 SubAgent 模式切换 UI，
包括按钮渲染、点击切换、状态文本、持久化验证。

运行方式：
  1. make dev  (启动前后端)
  2. cd backend && .venv/bin/pytest tests/e2e/test_sub_agent_mode_ui.py -v --tb=short

依赖：前后端均运行中（http://localhost:3000 + http://localhost:8099）。
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.helpers import (
    open_meta_panel,
    wait_for_chat_ready,
)

pytestmark = [pytest.mark.e2e]


class TestSubAgentModeUI:
    """SubAgent 调度模式 UI 交互测试。"""

    def test_sub_agent_mode_section_visible(self, page: Page):
        """会话信息面板中应显示 SubAgent 调度模式区域。"""
        wait_for_chat_ready(page)
        meta_panel = open_meta_panel(page)

        # 应存在 sub-agent-mode 区块
        mode_section = meta_panel.locator("[data-testid='sub-agent-mode']")
        expect(mode_section).to_be_visible(timeout=5000)

        # 标题文本
        expect(mode_section.get_by_text("SubAgent 调度模式")).to_be_visible()

    def test_four_mode_buttons_visible(self, page: Page):
        """应显示 4 个模式按钮：全局、自动、手动、关。"""
        wait_for_chat_ready(page)
        meta_panel = open_meta_panel(page)

        for test_id in [
            "sub-agent-btn-global",
            "sub-agent-btn-auto",
            "sub-agent-btn-manual",
            "sub-agent-btn-off",
        ]:
            btn = meta_panel.locator(f"[data-testid='{test_id}']")
            expect(btn).to_be_visible(timeout=3000)

    def test_default_mode_is_global(self, page: Page):
        """新 session 的默认模式应为「全局」（sub_agent_mode = null）。"""
        wait_for_chat_ready(page)
        meta_panel = open_meta_panel(page)

        global_btn = meta_panel.locator("[data-testid='sub-agent-btn-global']")
        expect(global_btn).to_have_attribute("aria-pressed", "true", timeout=5000)

        # 状态文本
        status = meta_panel.locator("[data-testid='sub-agent-status']")
        expect(status).to_contain_text("跟随全局")

    def test_switch_to_auto_mode(self, page: Page):
        """点击「自动」按钮应切换到自动模式。"""
        wait_for_chat_ready(page)
        meta_panel = open_meta_panel(page)

        auto_btn = meta_panel.locator("[data-testid='sub-agent-btn-auto']")
        auto_btn.click()
        page.wait_for_timeout(500)

        # 自动按钮应为 pressed
        expect(auto_btn).to_have_attribute("aria-pressed", "true", timeout=5000)

        # 全局按钮应为 not pressed
        global_btn = meta_panel.locator("[data-testid='sub-agent-btn-global']")
        expect(global_btn).to_have_attribute("aria-pressed", "false")

        # 状态文本
        status = meta_panel.locator("[data-testid='sub-agent-status']")
        expect(status).to_contain_text("自动模式")

    def test_switch_to_manual_mode(self, page: Page):
        """点击「手动」按钮应切换到手动模式并显示提示。"""
        wait_for_chat_ready(page)
        meta_panel = open_meta_panel(page)

        manual_btn = meta_panel.locator("[data-testid='sub-agent-btn-manual']")
        manual_btn.click()
        page.wait_for_timeout(500)

        expect(manual_btn).to_have_attribute("aria-pressed", "true", timeout=5000)

        # 状态文本
        status = meta_panel.locator("[data-testid='sub-agent-status']")
        expect(status).to_contain_text("手动模式")

        # 手动模式应有 @mention 使用提示
        expect(meta_panel.get_by_text("@researcher").first).to_be_visible()

    def test_switch_to_off_mode(self, page: Page):
        """点击「关」按钮应禁用 SubAgent 调度。"""
        wait_for_chat_ready(page)
        meta_panel = open_meta_panel(page)

        off_btn = meta_panel.locator("[data-testid='sub-agent-btn-off']")
        off_btn.click()
        page.wait_for_timeout(500)

        expect(off_btn).to_have_attribute("aria-pressed", "true", timeout=5000)

        # 状态文本
        status = meta_panel.locator("[data-testid='sub-agent-status']")
        expect(status).to_contain_text("已禁用")

    def test_switch_back_to_global(self, page: Page):
        """从其他模式切回「全局」按钮应生效。"""
        wait_for_chat_ready(page)
        meta_panel = open_meta_panel(page)

        # 先切到 auto
        auto_btn = meta_panel.locator("[data-testid='sub-agent-btn-auto']")
        auto_btn.click()
        page.wait_for_timeout(500)
        expect(auto_btn).to_have_attribute("aria-pressed", "true", timeout=5000)

        # 再切回 global
        global_btn = meta_panel.locator("[data-testid='sub-agent-btn-global']")
        global_btn.click()
        page.wait_for_timeout(500)

        expect(global_btn).to_have_attribute("aria-pressed", "true", timeout=5000)
        expect(auto_btn).to_have_attribute("aria-pressed", "false")

        status = meta_panel.locator("[data-testid='sub-agent-status']")
        expect(status).to_contain_text("跟随全局")

    def test_mode_persists_after_panel_reopen(self, page: Page):
        """切换模式后关闭再打开面板，模式应保持不变。"""
        wait_for_chat_ready(page)
        meta_panel = open_meta_panel(page)

        # 切换到 auto
        auto_btn = meta_panel.locator("[data-testid='sub-agent-btn-auto']")
        auto_btn.click()
        page.wait_for_timeout(800)
        expect(auto_btn).to_have_attribute("aria-pressed", "true", timeout=5000)

        # 关闭面板
        close_btn = page.locator("button[title='会话信息']")
        close_btn.click()
        page.wait_for_timeout(500)

        # 重新打开面板
        meta_panel = open_meta_panel(page)
        auto_btn = meta_panel.locator("[data-testid='sub-agent-btn-auto']")
        expect(auto_btn).to_have_attribute("aria-pressed", "true", timeout=5000)

        status = meta_panel.locator("[data-testid='sub-agent-status']")
        expect(status).to_contain_text("自动模式")

    def test_mode_persists_after_page_reload(self, page: Page):
        """切换模式后刷新页面，模式应保持不变（后端持久化）。"""
        wait_for_chat_ready(page)
        meta_panel = open_meta_panel(page)

        # 切换到 manual
        manual_btn = meta_panel.locator("[data-testid='sub-agent-btn-manual']")
        manual_btn.click()
        page.wait_for_timeout(1000)
        expect(manual_btn).to_have_attribute("aria-pressed", "true", timeout=5000)

        # 刷新页面
        page.reload()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)

        # 重新打开面板
        meta_panel = open_meta_panel(page)
        manual_btn = meta_panel.locator("[data-testid='sub-agent-btn-manual']")
        expect(manual_btn).to_have_attribute("aria-pressed", "true", timeout=5000)
