"""DingTalk Stream Worker 单元测试。"""

from __future__ import annotations

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpal.channels.dingtalk_stream_worker import (
    DingTalkStreamWorker,
    _extract_text,
    _handle_message,
    _send_reply,
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
    async def test_calls_assistant_and_replies(self):
        """正常文本消息应调用助手并发送回复。"""
        mock_ds = _make_mock_ds_module()
        msg = _make_chat_message("你好")
        mock_ds.ChatbotMessage.from_dict.return_value = msg

        with patch.dict(sys.modules, {"dingtalk_stream": mock_ds}), \
             patch(
                 "agentpal.channels.dingtalk_stream_worker._invoke_assistant",
                 new=AsyncMock(return_value="你好！有什么可以帮助你？"),
             ) as mock_invoke, \
             patch(
                 "agentpal.channels.dingtalk_stream_worker._send_reply",
                 new=AsyncMock(),
             ) as mock_reply:
            await _handle_message({})

        mock_invoke.assert_awaited_once_with("dingtalk:conv123", "你好")
        mock_reply.assert_awaited_once_with(
            "http://webhook.example.com/reply", "你好！有什么可以帮助你？"
        )

    @pytest.mark.asyncio
    async def test_empty_text_ignored(self):
        """纯空白文本不应触发助手调用。"""
        mock_ds = _make_mock_ds_module()
        msg = _make_chat_message("   ")
        mock_ds.ChatbotMessage.from_dict.return_value = msg

        with patch.dict(sys.modules, {"dingtalk_stream": mock_ds}), \
             patch(
                 "agentpal.channels.dingtalk_stream_worker._invoke_assistant",
                 new=AsyncMock(),
             ) as mock_invoke:
            await _handle_message({})

        mock_invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_at_prefix_stripped(self):
        """群消息中 @机器人 前缀应被去除后再传给助手。"""
        mock_ds = _make_mock_ds_module()
        msg = _make_chat_message("@nimo 帮我查一下天气")
        mock_ds.ChatbotMessage.from_dict.return_value = msg

        with patch.dict(sys.modules, {"dingtalk_stream": mock_ds}), \
             patch(
                 "agentpal.channels.dingtalk_stream_worker._invoke_assistant",
                 new=AsyncMock(return_value="好的"),
             ) as mock_invoke, \
             patch("agentpal.channels.dingtalk_stream_worker._send_reply", new=AsyncMock()):
            await _handle_message({})

        actual_text = mock_invoke.call_args[0][1]
        assert actual_text == "帮我查一下天气"

    @pytest.mark.asyncio
    async def test_no_session_webhook_does_not_call_reply(self):
        """缺少 session_webhook 时，不应调用 _send_reply，但也不应报错。"""
        mock_ds = _make_mock_ds_module()
        msg = _make_chat_message("hello")
        msg.session_webhook = None
        mock_ds.ChatbotMessage.from_dict.return_value = msg

        with patch.dict(sys.modules, {"dingtalk_stream": mock_ds}), \
             patch(
                 "agentpal.channels.dingtalk_stream_worker._invoke_assistant",
                 new=AsyncMock(return_value="pong"),
             ), \
             patch(
                 "agentpal.channels.dingtalk_stream_worker._send_reply",
                 new=AsyncMock(),
             ) as mock_reply:
            await _handle_message({})

        mock_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_sender_id_fallback_for_session_id(self):
        """conversation_id 为空时，应回退到 sender_id 构造 session_id。"""
        mock_ds = _make_mock_ds_module()
        msg = _make_chat_message("测试", conversation_id="", sender_id="sender_xyz")
        mock_ds.ChatbotMessage.from_dict.return_value = msg

        with patch.dict(sys.modules, {"dingtalk_stream": mock_ds}), \
             patch(
                 "agentpal.channels.dingtalk_stream_worker._invoke_assistant",
                 new=AsyncMock(return_value="ok"),
             ) as mock_invoke, \
             patch("agentpal.channels.dingtalk_stream_worker._send_reply", new=AsyncMock()):
            await _handle_message({})

        session_id = mock_invoke.call_args[0][0]
        assert session_id == "dingtalk:sender_xyz"


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
