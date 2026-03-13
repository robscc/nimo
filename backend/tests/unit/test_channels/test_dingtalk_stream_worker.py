"""DingTalk Stream Worker 单元测试。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpal.channels.dingtalk_stream_worker import (
    DingTalkStreamWorker,
    _ensure_dingtalk_session,
    _extract_text,
    _format_tool_done,
    _format_tool_start,
    _get_dingtalk_access_token,
    _handle_message,
    _send_markdown_reply,
    _send_reply,
    _stream_reply,
    _upload_to_dingtalk,
)

# ── 辅助：构造 mock dingtalk_stream 模块 ─────────────────


def _make_mock_ds_module(status_ok: int = 200) -> ModuleType:
    """构造轻量 mock dingtalk_stream 模块（含可继承的 ChatbotHandler 基类）。"""
    mod = ModuleType("dingtalk_stream")
    mod.Credential = MagicMock()
    mod.DingTalkStreamClient = MagicMock()
    mod.ChatbotMessage = MagicMock()
    mod.ChatbotMessage.TOPIC = "/v1.0/im/bot/messages/get"
    mod.AckMessage = MagicMock()
    mod.AckMessage.STATUS_OK = status_ok

    # ChatbotHandler 需是真实 class，以便 _SdkChatbotHandler 继承
    class _MockChatbotHandler:
        dingtalk_client = None

        def pre_start(self) -> None:
            pass

        async def raw_process(self, callback_message: object) -> MagicMock:
            code, msg = await self.process(callback_message)
            return MagicMock(code=code, data={"response": msg})

        async def process(self, callback_message: object) -> tuple[int, str]:
            return status_ok, "OK"

    mod.ChatbotHandler = _MockChatbotHandler
    return mod


def _make_chat_message(
    text_content: str = "你好",
    conversation_id: str = "conv123",
    sender_id: str = "sender001",
    sender_nick: str = "张三",
    session_webhook: str = "http://webhook.example.com/reply",
) -> MagicMock:
    """构造 mock ChatbotMessage 实例。"""
    msg = MagicMock()
    msg.text = MagicMock()
    msg.text.content = text_content
    msg.conversation_id = conversation_id
    msg.sender_id = sender_id
    msg.sender_nick = sender_nick
    msg.session_webhook = session_webhook
    return msg


# ── DingTalkStreamWorker ──────────────────────────────────


class TestDingTalkStreamWorker:
    @pytest.mark.asyncio
    async def test_start_when_disabled(self):
        worker = DingTalkStreamWorker()
        with patch("agentpal.channels.dingtalk_stream_worker.get_settings") as mock_cfg:
            mock_cfg.return_value.dingtalk_enabled = False
            await worker.start()
        assert worker._task is None
        assert not worker.running

    @pytest.mark.asyncio
    async def test_start_missing_app_key(self):
        worker = DingTalkStreamWorker()
        with patch("agentpal.channels.dingtalk_stream_worker.get_settings") as mock_cfg:
            s = mock_cfg.return_value
            s.dingtalk_enabled = True
            s.dingtalk_app_key = ""
            s.dingtalk_app_secret = "secret"
            await worker.start()
        assert worker._task is None

    @pytest.mark.asyncio
    async def test_start_missing_app_secret(self):
        worker = DingTalkStreamWorker()
        with patch("agentpal.channels.dingtalk_stream_worker.get_settings") as mock_cfg:
            s = mock_cfg.return_value
            s.dingtalk_enabled = True
            s.dingtalk_app_key = "key"
            s.dingtalk_app_secret = ""
            await worker.start()
        assert worker._task is None

    @pytest.mark.asyncio
    async def test_start_import_error(self):
        """dingtalk-stream 包未安装时，不崩溃，不创建 task。"""
        worker = DingTalkStreamWorker()
        with patch("agentpal.channels.dingtalk_stream_worker.get_settings") as mock_cfg, \
             patch.dict(sys.modules, {"dingtalk_stream": None}):
            s = mock_cfg.return_value
            s.dingtalk_enabled = True
            s.dingtalk_app_key = "appkey"
            s.dingtalk_app_secret = "appsecret"
            await worker.start()
        assert worker._task is None

    @pytest.mark.asyncio
    async def test_start_creates_background_task(self):
        """配置齐全时，应创建并运行后台任务。"""
        mock_ds = _make_mock_ds_module()
        mock_client = MagicMock()
        mock_client.start = AsyncMock(side_effect=asyncio.CancelledError)
        mock_ds.DingTalkStreamClient.return_value = mock_client

        worker = DingTalkStreamWorker()
        with patch("agentpal.channels.dingtalk_stream_worker.get_settings") as mock_cfg, \
             patch.dict(sys.modules, {"dingtalk_stream": mock_ds}):
            s = mock_cfg.return_value
            s.dingtalk_enabled = True
            s.dingtalk_app_key = "test_app_key"
            s.dingtalk_app_secret = "test_app_secret"

            await worker.start()
            assert worker._task is not None
            assert worker.running

            # 在 mock 上下文内停止任务
            await worker.stop()

        assert not worker.running

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        """未启动时调用 stop 不应报错。"""
        worker = DingTalkStreamWorker()
        await worker.stop()  # should not raise

    @pytest.mark.asyncio
    async def test_stop_cancels_running_task(self):
        """stop() 应取消后台 task，running 变 False。"""
        async def _forever():
            await asyncio.sleep(9999)

        worker = DingTalkStreamWorker()
        worker._task = asyncio.create_task(_forever())
        assert worker.running

        await worker.stop()
        assert not worker.running

    @pytest.mark.asyncio
    async def test_running_false_when_task_done(self):
        async def _noop():
            pass

        worker = DingTalkStreamWorker()
        worker._task = asyncio.create_task(_noop())
        await worker._task
        assert not worker.running


# ── _handle_message ───────────────────────────────────────


class TestHandleMessage:
    @pytest.mark.asyncio
    async def test_delegates_to_stream_reply(self):
        """正常消息应调用 _stream_reply。"""
        mock_ds = _make_mock_ds_module()
        msg = _make_chat_message("你好")
        mock_ds.ChatbotMessage.from_dict.return_value = msg

        with patch.dict(sys.modules, {"dingtalk_stream": mock_ds}), \
             patch(
                 "agentpal.channels.dingtalk_stream_worker._stream_reply",
                 new=AsyncMock(),
             ) as mock_stream:
            await _handle_message({})

        mock_stream.assert_awaited_once_with(
            "dingtalk:conv123", "你好", "http://webhook.example.com/reply"
        )

    @pytest.mark.asyncio
    async def test_empty_text_ignored(self):
        """纯空白文本不应触发助手调用。"""
        mock_ds = _make_mock_ds_module()
        msg = _make_chat_message("   ")
        mock_ds.ChatbotMessage.from_dict.return_value = msg

        with patch.dict(sys.modules, {"dingtalk_stream": mock_ds}), \
             patch(
                 "agentpal.channels.dingtalk_stream_worker._stream_reply",
                 new=AsyncMock(),
             ) as mock_stream:
            await _handle_message({})

        mock_stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_at_prefix_stripped(self):
        """群消息中 @机器人 前缀应被去除后再传给助手。"""
        mock_ds = _make_mock_ds_module()
        msg = _make_chat_message("@nimo 帮我查天气")
        mock_ds.ChatbotMessage.from_dict.return_value = msg

        with patch.dict(sys.modules, {"dingtalk_stream": mock_ds}), \
             patch(
                 "agentpal.channels.dingtalk_stream_worker._stream_reply",
                 new=AsyncMock(),
             ) as mock_stream:
            await _handle_message({})

        actual_text = mock_stream.call_args[0][1]
        assert actual_text == "帮我查天气"

    @pytest.mark.asyncio
    async def test_no_session_webhook_does_not_call_stream(self):
        """缺少 session_webhook 时，不应调用 _stream_reply。"""
        mock_ds = _make_mock_ds_module()
        msg = _make_chat_message("hello")
        msg.session_webhook = None
        mock_ds.ChatbotMessage.from_dict.return_value = msg

        with patch.dict(sys.modules, {"dingtalk_stream": mock_ds}), \
             patch(
                 "agentpal.channels.dingtalk_stream_worker._stream_reply",
                 new=AsyncMock(),
             ) as mock_stream:
            await _handle_message({})

        mock_stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_sender_id_fallback_for_session_id(self):
        """conversation_id 为空时，应回退到 sender_id 构造 session_id。"""
        mock_ds = _make_mock_ds_module()
        msg = _make_chat_message("测试", conversation_id="", sender_id="sender_xyz")
        mock_ds.ChatbotMessage.from_dict.return_value = msg

        with patch.dict(sys.modules, {"dingtalk_stream": mock_ds}), \
             patch(
                 "agentpal.channels.dingtalk_stream_worker._stream_reply",
                 new=AsyncMock(),
             ) as mock_stream:
            await _handle_message({})

        session_id = mock_stream.call_args[0][0]
        assert session_id == "dingtalk:sender_xyz"


# ── _ensure_dingtalk_session ───────────────────────────────


class TestEnsureDingtalkSession:
    @pytest.mark.asyncio
    async def test_executes_upsert_and_commits(self):
        """应执行 SQLite upsert 并 commit。"""
        mock_db = AsyncMock()
        await _ensure_dingtalk_session(mock_db, "dingtalk:conv123")

        mock_db.execute.assert_awaited_once()
        mock_db.commit.assert_awaited_once()

        # 验证 INSERT 语句包含正确的 session_id 和 channel
        stmt = mock_db.execute.call_args[0][0]
        # stmt 是 SQLAlchemy Insert 对象，编译后应包含关键值
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "dingtalk:conv123" in compiled
        assert "dingtalk" in compiled

    @pytest.mark.asyncio
    async def test_called_during_stream_reply(self):
        """_stream_reply 应在调用助手前先 upsert SessionRecord。"""
        async def _fake_stream(text):
            yield {"type": "text_delta", "delta": "hi"}
            yield {"type": "done"}

        mock_assistant = MagicMock()
        mock_assistant.reply_stream = _fake_stream
        mock_db_ctx = AsyncMock()

        with patch("agentpal.database.AsyncSessionLocal", return_value=mock_db_ctx), \
             patch("agentpal.memory.factory.MemoryFactory"), \
             patch("agentpal.agents.personal_assistant.PersonalAssistant",
                   return_value=mock_assistant), \
             patch("agentpal.channels.dingtalk_stream_worker.get_settings") as mock_cfg, \
             patch("agentpal.channels.dingtalk_stream_worker._ensure_dingtalk_session",
                   new=AsyncMock()) as mock_ensure, \
             patch("agentpal.channels.dingtalk_stream_worker._send_reply",
                   new=AsyncMock()):
            mock_cfg.return_value.memory_backend = "buffer"
            await _stream_reply("dingtalk:conv123", "你好", "http://webhook/reply")

        mock_ensure.assert_awaited_once_with(mock_db_ctx.__aenter__.return_value, "dingtalk:conv123")


# ── _stream_reply（渐进式发送）────────────────────────────


class TestStreamReply:
    @pytest.mark.asyncio
    async def test_tool_start_sends_markdown_immediately(self):
        """tool_start 应立即发送一条 Markdown 提示。"""
        async def _fake_stream(text):
            yield {"type": "tool_start", "name": "get_current_time", "input": {}}
            yield {"type": "tool_done", "name": "get_current_time",
                   "output": "13:00", "error": None, "duration_ms": 5}
            yield {"type": "text_delta", "delta": "现在是1点"}
            yield {"type": "done"}

        mock_assistant = MagicMock()
        mock_assistant.reply_stream = _fake_stream
        mock_db_ctx = AsyncMock()

        with patch("agentpal.database.AsyncSessionLocal", return_value=mock_db_ctx), \
             patch("agentpal.memory.factory.MemoryFactory"), \
             patch("agentpal.agents.personal_assistant.PersonalAssistant",
                   return_value=mock_assistant), \
             patch("agentpal.channels.dingtalk_stream_worker.get_settings") as mock_cfg, \
             patch("agentpal.channels.dingtalk_stream_worker._send_markdown_reply",
                   new=AsyncMock()) as mock_md, \
             patch("agentpal.channels.dingtalk_stream_worker._send_reply",
                   new=AsyncMock()) as mock_text:
            mock_cfg.return_value.memory_backend = "buffer"
            await _stream_reply("sid", "现在几点", "http://webhook/reply")

        # tool_start 发一条，tool_done 发一条
        assert mock_md.await_count == 2
        # 第一条是 tool_start
        first_md = mock_md.call_args_list[0]
        assert first_md[0][1] == "⏳ 工具调用"
        assert "get_current_time" in first_md[0][2]
        # 最终文本回复
        mock_text.assert_awaited_once()
        assert "现在是1点" in mock_text.call_args[0][1]

    @pytest.mark.asyncio
    async def test_no_tool_calls_only_text(self):
        """无工具调用时只发纯文本。"""
        async def _fake_stream(text):
            yield {"type": "text_delta", "delta": "你好！"}
            yield {"type": "done"}

        mock_assistant = MagicMock()
        mock_assistant.reply_stream = _fake_stream
        mock_db_ctx = AsyncMock()

        with patch("agentpal.database.AsyncSessionLocal", return_value=mock_db_ctx), \
             patch("agentpal.memory.factory.MemoryFactory"), \
             patch("agentpal.agents.personal_assistant.PersonalAssistant",
                   return_value=mock_assistant), \
             patch("agentpal.channels.dingtalk_stream_worker.get_settings") as mock_cfg, \
             patch("agentpal.channels.dingtalk_stream_worker._send_markdown_reply",
                   new=AsyncMock()) as mock_md, \
             patch("agentpal.channels.dingtalk_stream_worker._send_reply",
                   new=AsyncMock()) as mock_text:
            mock_cfg.return_value.memory_backend = "buffer"
            await _stream_reply("sid", "你好", "http://webhook/reply")

        mock_md.assert_not_called()
        mock_text.assert_awaited_once_with("http://webhook/reply", "你好！")

    @pytest.mark.asyncio
    async def test_error_event_sends_error_message(self):
        """error 事件应立即发送错误消息。"""
        async def _fake_stream(text):
            yield {"type": "error", "message": "模型调用失败"}
            yield {"type": "done"}

        mock_assistant = MagicMock()
        mock_assistant.reply_stream = _fake_stream
        mock_db_ctx = AsyncMock()

        with patch("agentpal.database.AsyncSessionLocal", return_value=mock_db_ctx), \
             patch("agentpal.memory.factory.MemoryFactory"), \
             patch("agentpal.agents.personal_assistant.PersonalAssistant",
                   return_value=mock_assistant), \
             patch("agentpal.channels.dingtalk_stream_worker.get_settings") as mock_cfg, \
             patch("agentpal.channels.dingtalk_stream_worker._send_reply",
                   new=AsyncMock()) as mock_text:
            mock_cfg.return_value.memory_backend = "buffer"
            await _stream_reply("sid", "test", "http://webhook/reply")

        calls = mock_text.call_args_list
        assert any("❌ 模型调用失败" in str(c) for c in calls)


# ── _format_tool_start / _format_tool_done ────────────────


class TestFormatToolStart:
    def test_basic(self):
        md = _format_tool_start("get_current_time", {})
        assert "⏳" in md
        assert "**get_current_time**" in md
        assert "{}" in md

    def test_long_input_truncated(self):
        long_input = {"data": "x" * 300}
        md = _format_tool_start("tool", long_input)
        assert "…" in md

    def test_non_dict_input(self):
        md = _format_tool_start("tool", "simple_string")
        assert "simple_string" in md


class TestFormatToolDone:
    def test_success(self):
        event = {
            "name": "get_current_time",
            "output": "2026-03-13 13:21:00",
            "error": None,
            "duration_ms": 12,
        }
        md = _format_tool_done(event)
        assert "🔧 **get_current_time**" in md
        assert "输出：2026-03-13 13:21:00" in md
        assert "⏱ 12ms" in md

    def test_error(self):
        event = {
            "name": "execute_shell_command",
            "output": "",
            "error": "Permission denied",
            "duration_ms": 1,
        }
        md = _format_tool_done(event)
        assert "❌ 错误：Permission denied" in md
        assert "输出" not in md

    def test_long_output_truncated(self):
        event = {
            "name": "read_file",
            "output": "x" * 600,
            "error": None,
            "duration_ms": 10,
        }
        md = _format_tool_done(event)
        assert "…" in md

    def test_no_duration(self):
        event = {
            "name": "tool",
            "output": "ok",
            "error": None,
            "duration_ms": None,
        }
        md = _format_tool_done(event)
        assert "⏱" not in md


# ── _extract_text ─────────────────────────────────────────


class TestExtractText:
    def _msg(self, content: str) -> MagicMock:
        m = MagicMock()
        m.text = MagicMock()
        m.text.content = content
        return m

    def test_plain_text(self):
        assert _extract_text(self._msg("hello world")) == "hello world"

    def test_strip_whitespace(self):
        assert _extract_text(self._msg("  hi  ")) == "hi"

    def test_strip_single_at(self):
        assert _extract_text(self._msg("@nimo 帮我")) == "帮我"

    def test_strip_multiple_at(self):
        assert _extract_text(self._msg("@nimo @bot 帮我查")) == "帮我查"

    def test_no_text_attribute(self):
        msg = MagicMock(spec=[])  # no 'text' attr
        assert _extract_text(msg) == ""

    def test_text_content_is_none(self):
        m = MagicMock()
        m.text.content = None
        assert _extract_text(m) == ""

    def test_only_at_prefix_returns_empty(self):
        assert _extract_text(self._msg("@nimo ")) == ""

    def test_at_in_middle_not_stripped(self):
        """@ 不在开头则不应被去除。"""
        result = _extract_text(self._msg("帮我 @nimo 查天气"))
        assert "@nimo" in result


# ── _send_reply ───────────────────────────────────────────


class TestSendReply:
    @pytest.mark.asyncio
    async def test_posts_to_webhook_url(self):
        with patch("httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            await _send_reply("http://webhook.test/reply", "Hello!")

        post = mock_client.return_value.__aenter__.return_value.post
        post.assert_awaited_once()
        call_args = post.call_args
        assert call_args[0][0] == "http://webhook.test/reply"
        payload = call_args[1]["json"]
        assert payload["msgtype"] == "text"
        assert payload["text"]["content"] == "Hello!"

    @pytest.mark.asyncio
    async def test_non_200_does_not_raise(self):
        """HTTP 非 200 只警告，不抛异常。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            await _send_reply("http://webhook.test/reply", "Hello!")  # must not raise

    @pytest.mark.asyncio
    async def test_network_exception_does_not_raise(self):
        """网络异常只记日志，不向上传播。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=Exception("connection refused")
            )
            await _send_reply("http://webhook.test/reply", "Hello!")  # must not raise


# ── _send_markdown_reply ──────────────────────────────────


class TestSendMarkdownReply:
    @pytest.mark.asyncio
    async def test_posts_markdown_to_webhook(self):
        """应以 markdown msgtype 发送。"""
        with patch("httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            await _send_markdown_reply(
                "http://webhook.test/reply", "AI 助手", "# Hello\n正文"
            )

        post = mock_client.return_value.__aenter__.return_value.post
        post.assert_awaited_once()
        call_args = post.call_args
        assert call_args[0][0] == "http://webhook.test/reply"
        payload = call_args[1]["json"]
        assert payload["msgtype"] == "markdown"
        assert payload["markdown"]["title"] == "AI 助手"
        assert payload["markdown"]["text"] == "# Hello\n正文"

    @pytest.mark.asyncio
    async def test_non_200_does_not_raise(self):
        with patch("httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            await _send_markdown_reply("http://wh/r", "AI", "text")

    @pytest.mark.asyncio
    async def test_network_exception_does_not_raise(self):
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                side_effect=Exception("timeout")
            )
            await _send_markdown_reply("http://wh/r", "AI", "text")


# ── _get_dingtalk_access_token ────────────────────────────


class TestGetAccessToken:
    @pytest.mark.asyncio
    async def test_success(self):
        with patch("httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"errcode": 0, "access_token": "tok123"}
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_resp
            )
            token = await _get_dingtalk_access_token("key", "secret")
        assert token == "tok123"

    @pytest.mark.asyncio
    async def test_failure_returns_none(self):
        with patch("httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"errcode": 40001, "errmsg": "invalid"}
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_resp
            )
            token = await _get_dingtalk_access_token("key", "secret")
        assert token is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=Exception("network")
            )
            token = await _get_dingtalk_access_token("key", "secret")
        assert token is None


# ── _upload_to_dingtalk ───────────────────────────────────


class TestUploadToDingtalk:
    @pytest.mark.asyncio
    async def test_success(self, tmp_path: Path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG")

        with patch(
            "agentpal.channels.dingtalk_stream_worker._get_dingtalk_access_token",
            new=AsyncMock(return_value="tok123"),
        ), patch("httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"errcode": 0, "media_id": "@lAD123456"}
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            media_id = await _upload_to_dingtalk("key", "secret", img, "image/png")

        assert media_id == "@lAD123456"

    @pytest.mark.asyncio
    async def test_no_token_returns_none(self, tmp_path: Path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG")

        with patch(
            "agentpal.channels.dingtalk_stream_worker._get_dingtalk_access_token",
            new=AsyncMock(return_value=None),
        ):
            media_id = await _upload_to_dingtalk("key", "secret", img, "image/png")

        assert media_id is None

    @pytest.mark.asyncio
    async def test_upload_failure_returns_none(self, tmp_path: Path):
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG")

        with patch(
            "agentpal.channels.dingtalk_stream_worker._get_dingtalk_access_token",
            new=AsyncMock(return_value="tok123"),
        ), patch("httpx.AsyncClient") as mock_client:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"errcode": 40001, "errmsg": "fail"}
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(
                return_value=mock_resp
            )
            media_id = await _upload_to_dingtalk("key", "secret", img, "image/png")

        assert media_id is None
