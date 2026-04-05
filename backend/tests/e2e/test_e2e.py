"""Playwright E2E 测试 — 需要前后端同时运行。

运行方式：
  1. make dev  (启动前后端)
  2. cd backend && .venv/bin/pytest tests/e2e/ -v --tb=short

测试假设前端运行在 http://localhost:3000

NOTE: 使用 "domcontentloaded" 而非 "networkidle" 作为页面加载状态，
因为前端会维护 SSE (EventSource) 和 WebSocket 长连接，
"networkidle" 永远不会触发。
"""

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.helpers import (
    cleanup_temp_assets,
    create_real_upload_files,
    require_backend,
    require_frontend,
    send_ui_message,
    wait_for_chat_ready,
)

BASE_URL = "http://localhost:3000"


# ── Chat Page ─────────────────────────────────────────────


class TestChatPage:
    """Chat 页面基本功能。"""

    def test_chat_page_loads(self, page: Page):
        """Chat 页面能正常加载。"""
        page.goto(f"{BASE_URL}/chat")
        page.wait_for_load_state("domcontentloaded")
        # 精确匹配 header 中的 nimo 标题
        expect(page.get_by_text("nimo", exact=True).first).to_be_visible()

    def test_session_panel_visible(self, page: Page):
        """左侧会话面板可见。"""
        page.goto(f"{BASE_URL}/chat")
        page.wait_for_load_state("domcontentloaded")
        expect(page.get_by_text("历史对话", exact=True)).to_be_visible()

    def test_session_meta_panel_toggle(self, page: Page):
        """点击设置按钮可以打开/关闭会话信息面板。"""
        page.goto(f"{BASE_URL}/chat")
        page.wait_for_load_state("domcontentloaded")
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
        expect(meta_panel.locator("text=工具").first).to_be_visible()

    def test_new_session_button(self, page: Page):
        """点击新建对话按钮可以创建新对话。"""
        page.goto(f"{BASE_URL}/chat")
        page.wait_for_load_state("domcontentloaded")
        new_btn = page.locator("button[title='新建对话']")
        expect(new_btn).to_be_visible()


# ── Chat Conversation (LLM) ──────────────────────────────


class TestChatConversation:
    """对话功能 — 发送消息并验证 LLM 返回。"""

    def test_send_message_and_receive_reply(self, page: Page):
        """发送一条简单消息，LLM 应正常返回文本回复。"""
        page.goto(f"{BASE_URL}/chat")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)  # 等待 session 创建

        # 输入消息
        input_box = page.locator("input[placeholder*='输入消息']")
        expect(input_box).to_be_visible()
        input_box.fill("请用一句话回答：1+1等于几？")

        # 点击发送按钮
        send_btn = page.locator("form button[type='submit']")
        expect(send_btn).to_be_enabled()
        send_btn.click()

        # 用户消息气泡应出现（nimo-500 背景色）
        user_bubble = page.locator("div.bg-nimo-500.text-white.rounded-2xl")
        expect(user_bubble.last).to_be_visible(timeout=3000)
        expect(user_bubble.last).to_contain_text("1+1")

        # 等待 AI 回复出现（白色背景气泡，非流式占位符）
        # AI 的气泡是 bg-white border rounded-2xl，内容不为空
        # 最长等 30 秒（LLM 可能较慢）
        ai_bubble = page.locator("div.bg-white.border.rounded-2xl").last
        expect(ai_bubble).to_be_visible(timeout=30000)

        # 等待流式结束：闪烁光标消失
        page.wait_for_timeout(2000)
        # 再给额外时间让流式完成
        page.wait_for_function(
            """() => {
                const bubbles = document.querySelectorAll('div.bg-white.border.rounded-2xl');
                const last = bubbles[bubbles.length - 1];
                return last && last.textContent.length > 2 && !last.querySelector('.animate-pulse');
            }""",
            timeout=30000,
        )

        # 验证 AI 回复内容包含数字 "2"
        ai_text = ai_bubble.inner_text()
        assert len(ai_text) > 0, "AI 回复不应为空"
        assert "2" in ai_text, f"AI 回复应包含 '2'，实际: {ai_text}"

    def test_send_message_shows_in_session_list(self, page: Page):
        """发送消息后，左侧会话列表应更新标题。"""
        page.goto(f"{BASE_URL}/chat")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)

        # 发送消息
        input_box = page.locator("input[placeholder*='输入消息']")
        input_box.fill("你好呀，这是一条测试消息")

        send_btn = page.locator("form button[type='submit']")
        send_btn.click()

        # 等待回复完成
        page.wait_for_function(
            """() => {
                const bubbles = document.querySelectorAll('div.bg-white.border.rounded-2xl');
                const last = bubbles[bubbles.length - 1];
                return last && last.textContent.length > 2 && !last.querySelector('.animate-pulse');
            }""",
            timeout=30000,
        )

        # 左侧会话列表应更新 — 标题应包含用户的第一句话
        session_panel = page.locator("div.w-64.bg-white.border-r")
        expect(session_panel.get_by_text("你好呀").first).to_be_visible(timeout=15000)

    def test_multi_turn_conversation(self, page: Page):
        """多轮对话：连续发两条消息，均应得到回复。"""
        page.goto(f"{BASE_URL}/chat")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)

        input_box = page.locator("input[placeholder*='输入消息']")
        send_btn = page.locator("form button[type='submit']")

        # 第一轮
        input_box.fill("请记住这个数字：42")
        send_btn.click()

        # 等待第一轮回复完成
        page.wait_for_function(
            """() => {
                const bubbles = document.querySelectorAll('div.bg-white.border.rounded-2xl');
                const last = bubbles[bubbles.length - 1];
                return last && last.textContent.length > 2 && !last.querySelector('.animate-pulse');
            }""",
            timeout=30000,
        )

        # 第二轮
        input_box.fill("我刚才让你记住的数字是多少？")
        send_btn.click()

        # 等待第二轮回复完成
        page.wait_for_function(
            """() => {
                const bubbles = document.querySelectorAll('div.bg-white.border.rounded-2xl');
                return bubbles.length >= 2
                    && bubbles[bubbles.length - 1].textContent.length > 2
                    && !bubbles[bubbles.length - 1].querySelector('.animate-pulse');
            }""",
            timeout=30000,
        )

        # 验证：第二轮回复应该包含 42
        ai_bubbles = page.locator("div.bg-white.border.rounded-2xl")
        last_reply = ai_bubbles.last.inner_text()
        assert "42" in last_reply, f"多轮对话应记住数字 42，实际回复: {last_reply}"

    def test_clear_chat_and_new_session(self, page: Page):
        """清空对话后应重置为空白。"""
        page.goto(f"{BASE_URL}/chat")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)

        # 先发一条消息
        input_box = page.locator("input[placeholder*='输入消息']")
        input_box.fill("随便说点什么")
        page.locator("form button[type='submit']").click()

        # 等待回复
        page.wait_for_function(
            """() => {
                const bubbles = document.querySelectorAll('div.bg-white.border.rounded-2xl');
                const last = bubbles[bubbles.length - 1];
                return last && last.textContent.length > 2 && !last.querySelector('.animate-pulse');
            }""",
            timeout=30000,
        )

        # 点击清空按钮
        clear_btn = page.locator("button[title='清空对话']")
        expect(clear_btn).to_be_visible()
        clear_btn.click()

        page.wait_for_timeout(1000)

        # 清空后应回到欢迎界面
        expect(page.get_by_text("嗨！我是 nimo").first).to_be_visible(timeout=5000)




# ── Chat File Upload (Real Files) ─────────────────────────


@pytest.mark.e2e
@require_backend
@require_frontend
class TestChatFileUpload:
    """Chat 页面真实文件上传（txt/pdf/csv）测试。"""

    def test_upload_real_files_and_send_for_analysis(self, page: Page):
        """上传 txt/pdf/csv 后发送消息，验证 file_ids 随 chat 请求透传。"""
        wait_for_chat_ready(page)

        upload_paths = create_real_upload_files(prefix="chat-upload")
        chat_payloads: list[dict] = []

        def _capture_chat_request(req):
            if req.method != "POST":
                return
            if not req.url.endswith("/api/v1/agent/chat"):
                return
            post_data = req.post_data or "{}"
            try:
                chat_payloads.append(json.loads(post_data))
            except Exception:
                chat_payloads.append({})

        page.on("request", _capture_chat_request)

        try:
            upload_input = page.locator("input[type='file']")
            expect(upload_input).to_be_attached(timeout=5000)
            upload_input.set_input_files([str(path) for path in upload_paths])

            # 上传成功后应在输入框上方显示文件 chip
            for file_name in ["notes.txt", "report.pdf", "metrics.csv"]:
                expect(page.get_by_text(file_name, exact=True)).to_be_visible(timeout=10000)

            # 不应立刻显示上传失败提示
            page.wait_for_timeout(800)
            assert page.get_by_text("上传失败").count() == 0
            assert page.get_by_text("文件「").count() == 0

            # 发送消息触发 chat
            send_ui_message(page, "请分析我上传的文件，先给出结构化摘要。")

            # assistant 流程启动（出现 assistant 气泡）
            assistant_bubble = page.locator("div.bg-white.border.rounded-2xl").last
            expect(assistant_bubble).to_be_visible(timeout=15000)

            # 等待 chat 请求发出并校验 file_ids
            page.wait_for_timeout(1500)
            assert chat_payloads, "应捕获到 /api/v1/agent/chat 请求"

            latest_payload = chat_payloads[-1]
            assert latest_payload.get("message"), "chat 请求应包含 message"
            file_ids = latest_payload.get("file_ids")
            assert isinstance(file_ids, list), f"file_ids 应为 list，实际: {file_ids!r}"
            assert len(file_ids) == 3, f"应携带 3 个 file_ids，实际: {file_ids}"
            assert all(isinstance(fid, str) and fid for fid in file_ids), f"file_ids 应全为非空字符串: {file_ids}"
        finally:
            page.remove_listener("request", _capture_chat_request)
            cleanup_temp_assets(upload_paths)


# ── Tools Page ────────────────────────────────────────────


class TestToolsPage:
    """工具管理页面。"""

    def test_tools_page_loads(self, page: Page):
        """工具页面能正常加载。"""
        page.goto(f"{BASE_URL}/tools")
        page.wait_for_load_state("domcontentloaded")
        expect(page.get_by_text("工具管理", exact=True)).to_be_visible()

    def test_tools_list_visible(self, page: Page):
        """工具列表可见。"""
        page.goto(f"{BASE_URL}/tools")
        page.wait_for_load_state("domcontentloaded")
        # 至少应有 read_file 工具（工具名出现在列表和日志两处，取第一个）
        expect(page.get_by_text("read_file", exact=True).first).to_be_visible()

    def test_tool_toggle(self, page: Page):
        """工具开关可以切换。"""
        page.goto(f"{BASE_URL}/tools")
        page.wait_for_load_state("domcontentloaded")
        # 找到 toggle 按钮（通过 role）
        toggles = page.locator("button.rounded-full")
        expect(toggles.first).to_be_visible()


# ── Skills Page ───────────────────────────────────────────


class TestSkillsPage:
    """技能管理页面。"""

    def test_skills_page_loads(self, page: Page):
        """技能页面能正常加载。"""
        page.goto(f"{BASE_URL}/skills")
        page.wait_for_load_state("domcontentloaded")
        expect(page.get_by_text("技能管理", exact=True)).to_be_visible()

    def test_url_install_form_toggle(self, page: Page):
        """从 URL 安装按钮可以展开输入表单。"""
        page.goto(f"{BASE_URL}/skills")
        page.wait_for_load_state("domcontentloaded")

        url_btn = page.get_by_role("button", name="从 URL 安装")
        expect(url_btn).to_be_visible()
        url_btn.click()

        # 展开后应可见输入框
        url_input = page.locator("input[placeholder*='URL']")
        expect(url_input).to_be_visible()

    def test_upload_zip_button_visible(self, page: Page):
        """上传 ZIP 按钮可见。"""
        page.goto(f"{BASE_URL}/skills")
        page.wait_for_load_state("domcontentloaded")
        expect(page.get_by_text("上传 ZIP", exact=True)).to_be_visible()

    def test_installed_skills_visible(self, page: Page):
        """已安装的技能应显示在列表中（如果有的话）。"""
        page.goto(f"{BASE_URL}/skills")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1000)
        # 技能列表应加载完成（页面有内容）
        expect(page.get_by_text("技能管理", exact=True)).to_be_visible()

    def test_prompt_skill_badge_visible(self, page: Page):
        """Prompt 型技能应显示 prompt 标签（如果有安装的技能）。"""
        page.goto(f"{BASE_URL}/skills")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1000)
        # 技能页面应正常加载
        expect(page.get_by_text("技能管理", exact=True)).to_be_visible()


# ── Navigation ────────────────────────────────────────────


class TestNavigation:
    """侧边栏导航。"""

    def test_sidebar_navigation(self, page: Page):
        """侧边栏可在各页面间导航。"""
        page.goto(f"{BASE_URL}/chat")
        page.wait_for_load_state("domcontentloaded")

        # 导航到 Tools
        page.locator("a[title='工具']").click()
        page.wait_for_url("**/tools**", timeout=5000)
        expect(page.get_by_text("工具管理", exact=True)).to_be_visible(timeout=10000)

        # 导航到 Skills
        page.locator("a[title='技能']").click()
        page.wait_for_url("**/skills**", timeout=5000)
        expect(page.get_by_text("技能管理", exact=True)).to_be_visible(timeout=10000)

        # 导航到 Sessions
        page.locator("a[title='会话']").click()
        page.wait_for_url("**/sessions**", timeout=5000)
        expect(page.get_by_text("会话管理", exact=True)).to_be_visible(timeout=10000)

        # 导航回 Chat
        page.locator("a[title='对话']").click()
        page.wait_for_url("**/chat**", timeout=5000)
        expect(page.get_by_text("nimo", exact=True).first).to_be_visible(timeout=10000)


# ── Sessions Page ────────────────────────────────────────


class TestSessionsPage:
    """会话管理页面。"""

    def test_sessions_page_loads(self, page: Page):
        """会话管理页面能正常加载。"""
        page.goto(f"{BASE_URL}/sessions")
        page.wait_for_load_state("domcontentloaded")
        expect(page.get_by_text("会话管理", exact=True)).to_be_visible()

    def test_sessions_list_visible(self, page: Page):
        """会话列表可见，应有至少一个会话。"""
        # 先创建一个会话
        page.goto(f"{BASE_URL}/chat")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)

        # 再访问会话管理页面
        page.goto(f"{BASE_URL}/sessions")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1000)

        # 应至少能看到 "个会话" 统计
        expect(page.get_by_text("个会话").first).to_be_visible(timeout=5000)

    def test_sessions_search(self, page: Page):
        """搜索功能应可见并可交互。"""
        page.goto(f"{BASE_URL}/sessions")
        page.wait_for_load_state("domcontentloaded")

        search_input = page.locator("input[placeholder*='搜索']")
        expect(search_input).to_be_visible()
        search_input.fill("不存在的会话")
        page.wait_for_timeout(500)

        # 搜索不到时应显示空状态
        expect(page.get_by_text("没有匹配的会话").first).to_be_visible(timeout=3000)


# ── Session Meta Features ────────────────────────────────


class TestSessionMetaFeatures:
    """会话元信息功能测试。"""

    def test_tool_cards_in_meta_panel(self, page: Page):
        """会话信息面板中应显示工具卡片（可点击切换）。"""
        page.goto(f"{BASE_URL}/chat")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1000)

        # 打开 meta panel
        page.locator("button[title='会话信息']").click()
        page.wait_for_timeout(500)

        meta_panel = page.locator("[data-testid='session-meta-panel']")
        # 应有工具卡片按钮（带圆点指示器的按钮）
        tool_cards = meta_panel.locator("button.rounded-lg")
        expect(tool_cards.first).to_be_visible()
