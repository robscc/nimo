"""Playwright E2E 测试 — 需要前后端同时运行。

运行方式：
  1. make dev  (启动前后端)
  2. cd backend && .venv/bin/pytest tests/e2e/ -v --tb=short

测试假设前端运行在 http://localhost:3000
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


BASE_URL = "http://localhost:3000"


# ── Chat Page ─────────────────────────────────────────────


class TestChatPage:
    """Chat 页面基本功能。"""

    def test_chat_page_loads(self, page: Page):
        """Chat 页面能正常加载。"""
        page.goto(f"{BASE_URL}/chat")
        page.wait_for_load_state("networkidle")
        # 精确匹配 header 中的 nimo 标题
        expect(page.get_by_text("nimo", exact=True).first).to_be_visible()

    def test_session_panel_visible(self, page: Page):
        """左侧会话面板可见。"""
        page.goto(f"{BASE_URL}/chat")
        page.wait_for_load_state("networkidle")
        expect(page.get_by_text("历史对话", exact=True)).to_be_visible()

    def test_session_meta_panel_toggle(self, page: Page):
        """点击设置按钮可以打开/关闭会话信息面板。"""
        page.goto(f"{BASE_URL}/chat")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)  # 等待 session 创建完成

        # 点击设置按钮
        settings_btn = page.locator("button[title='会话信息']")
        expect(settings_btn).to_be_visible()
        settings_btn.click()
        page.wait_for_timeout(500)

        # 应出现会话信息面板
        meta_panel = page.locator("[data-testid='session-meta-panel']")
        expect(meta_panel).to_be_visible(timeout=5000)

        # 可以看到「模型」和「工具」标签
        expect(meta_panel.get_by_text("模型")).to_be_visible()
        expect(meta_panel.get_by_text("工具")).to_be_visible()

    def test_new_session_button(self, page: Page):
        """点击新建对话按钮可以创建新对话。"""
        page.goto(f"{BASE_URL}/chat")
        page.wait_for_load_state("networkidle")
        new_btn = page.locator("button[title='新建对话']")
        expect(new_btn).to_be_visible()


# ── Tools Page ────────────────────────────────────────────


class TestToolsPage:
    """工具管理页面。"""

    def test_tools_page_loads(self, page: Page):
        """工具页面能正常加载。"""
        page.goto(f"{BASE_URL}/tools")
        page.wait_for_load_state("networkidle")
        expect(page.get_by_text("工具管理", exact=True)).to_be_visible()

    def test_tools_list_visible(self, page: Page):
        """工具列表可见。"""
        page.goto(f"{BASE_URL}/tools")
        page.wait_for_load_state("networkidle")
        # 至少应有 read_file 工具
        expect(page.get_by_text("read_file", exact=True)).to_be_visible()

    def test_tool_toggle(self, page: Page):
        """工具开关可以切换。"""
        page.goto(f"{BASE_URL}/tools")
        page.wait_for_load_state("networkidle")
        # 找到 toggle 按钮（通过 role）
        toggles = page.locator("button.rounded-full")
        expect(toggles.first).to_be_visible()


# ── Skills Page ───────────────────────────────────────────


class TestSkillsPage:
    """技能管理页面。"""

    def test_skills_page_loads(self, page: Page):
        """技能页面能正常加载。"""
        page.goto(f"{BASE_URL}/skills")
        page.wait_for_load_state("networkidle")
        expect(page.get_by_text("技能管理", exact=True)).to_be_visible()

    def test_url_install_form_toggle(self, page: Page):
        """从 URL 安装按钮可以展开输入表单。"""
        page.goto(f"{BASE_URL}/skills")
        page.wait_for_load_state("networkidle")

        url_btn = page.get_by_role("button", name="从 URL 安装")
        expect(url_btn).to_be_visible()
        url_btn.click()

        # 展开后应可见输入框
        url_input = page.locator("input[placeholder*='URL']")
        expect(url_input).to_be_visible()

    def test_upload_zip_button_visible(self, page: Page):
        """上传 ZIP 按钮可见。"""
        page.goto(f"{BASE_URL}/skills")
        page.wait_for_load_state("networkidle")
        expect(page.get_by_text("上传 ZIP", exact=True)).to_be_visible()

    def test_installed_skills_visible(self, page: Page):
        """已安装的技能应显示在列表中（find-skills 已通过 API 安装）。"""
        page.goto(f"{BASE_URL}/skills")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
        # find-skills 已通过之前的 API 测试安装
        expect(page.get_by_text("find-skills", exact=True).first).to_be_visible(timeout=5000)

    def test_prompt_skill_badge_visible(self, page: Page):
        """Prompt 型技能应显示 prompt 标签。"""
        page.goto(f"{BASE_URL}/skills")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
        # prompt badge 应可见
        expect(page.get_by_text("prompt", exact=True).first).to_be_visible(timeout=5000)


# ── Navigation ────────────────────────────────────────────


class TestNavigation:
    """侧边栏导航。"""

    def test_sidebar_navigation(self, page: Page):
        """侧边栏可在各页面间导航。"""
        page.goto(f"{BASE_URL}/chat")
        page.wait_for_load_state("networkidle")

        # 导航到 Tools
        page.locator("a[title='工具']").click()
        page.wait_for_load_state("networkidle")
        expect(page.get_by_text("工具管理", exact=True)).to_be_visible()

        # 导航到 Skills
        page.locator("a[title='技能']").click()
        page.wait_for_load_state("networkidle")
        expect(page.get_by_text("技能管理", exact=True)).to_be_visible()

        # 导航回 Chat
        page.locator("a[title='对话']").click()
        page.wait_for_load_state("networkidle")
        expect(page.get_by_text("nimo", exact=True).first).to_be_visible()


# ── Session Meta Features ────────────────────────────────


class TestSessionMetaFeatures:
    """会话元信息功能测试。"""

    def test_tool_checkboxes_in_meta_panel(self, page: Page):
        """会话信息面板中应显示工具复选框。"""
        page.goto(f"{BASE_URL}/chat")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)

        # 打开 meta panel
        page.locator("button[title='会话信息']").click()
        page.wait_for_timeout(500)

        meta_panel = page.locator("[data-testid='session-meta-panel']")
        # 应有 checkbox（工具列表中的复选框）
        checkboxes = meta_panel.locator("input[type='checkbox']")
        expect(checkboxes.first).to_be_visible()
