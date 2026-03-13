"""DingTalk Stream 模式机器人 Worker。

架构：
    DingTalk 云 --WebSocket--> _SdkChatbotHandler.process()
                                          |
                                  _handle_message()
                                          |
                              PersonalAssistant.reply()
                                          |
                      sessionWebhook --HTTP--> DingTalk 云

配置（.env 或 ~/.nimo/config.yaml）：
    DINGTALK_ENABLED=true
    DINGTALK_APP_KEY=<ClientID / AppKey>
    DINGTALK_APP_SECRET=<ClientSecret / AppSecret>

注意：Stream 模式无需公网 IP，服务方主动连接钉钉云，适合本地/内网部署。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger

from agentpal.config import get_settings


class DingTalkStreamWorker:
    """管理 DingTalk Stream 客户端的后台 asyncio 任务生命周期。

    - start()：配置检查通过后创建后台任务，SDK 内部自动处理重连。
    - stop() ：取消后台任务，优雅地断开 WebSocket 连接。
    """

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """检查配置并在后台启动 DingTalk Stream 客户端。"""
        settings = get_settings()

        if not settings.dingtalk_enabled:
            logger.info("DingTalk Stream: dingtalk_enabled=False，跳过启动")
            return

        if not settings.dingtalk_app_key or not settings.dingtalk_app_secret:
            logger.warning(
                "DingTalk Stream: 缺少 DINGTALK_APP_KEY 或 DINGTALK_APP_SECRET，跳过启动。"
                "请在 .env 或 ~/.nimo/config.yaml 中配置后重启服务。"
            )
            return

        try:
            import dingtalk_stream  # noqa: F401
        except ImportError:
            logger.error(
                "DingTalk Stream: 未安装 dingtalk-stream 包，"
                "请执行：pip install 'dingtalk-stream>=0.24.0'"
            )
            return

        self._task = asyncio.create_task(
            _run_stream_client(settings.dingtalk_app_key, settings.dingtalk_app_secret),
            name="dingtalk-stream-client",
        )
        logger.info(
            f"DingTalk Stream 客户端已启动 ✅  "
            f"(app_key={settings.dingtalk_app_key[:8]}…)"
        )

    async def stop(self) -> None:
        """取消后台任务，等待清理完成。"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        logger.info("DingTalk Stream 客户端已停止")


# ── SDK 客户端（在 dingtalk_stream 可用时才执行）────────────


async def _run_stream_client(app_key: str, app_secret: str) -> None:
    """启动 DingTalk Stream 客户端并持续监听（SDK 内部处理断线重连）。"""
    import dingtalk_stream

    class _SdkChatbotHandler(dingtalk_stream.ChatbotHandler):
        """SDK 适配层：将异步逻辑委托给模块级 _handle_message()。"""

        async def process(self, callback: Any) -> tuple[int, str]:
            try:
                await _handle_message(callback.data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(f"DingTalk Stream: 消息处理异常 — {exc}")
            return dingtalk_stream.AckMessage.STATUS_OK, "OK"

    class _ConnectLogSystemHandler(dingtalk_stream.SystemHandler):
        """系统消息处理器：首次收到系统消息时记录 WebSocket 连接成功日志。"""

        def __init__(self) -> None:
            super().__init__()
            self._connected = False

        async def process(self, message: Any) -> tuple[int, str]:
            if not self._connected:
                self._connected = True
                logger.info("DingTalk Stream: WebSocket 连接成功 ✅，等待消息中…")
            return dingtalk_stream.AckMessage.STATUS_OK, "OK"

    credential = dingtalk_stream.Credential(app_key, app_secret)
    client = dingtalk_stream.DingTalkStreamClient(credential)
    client.system_handler = _ConnectLogSystemHandler()
    client.register_callback_handler(
        dingtalk_stream.ChatbotMessage.TOPIC,
        _SdkChatbotHandler(),
    )
    logger.info("DingTalk Stream: 正在连接…")
    await client.start()  # SDK 内部 while True 处理重连


# ── 核心消息处理逻辑（纯函数，方便单元测试）─────────────────


async def _handle_message(callback_data: dict[str, Any]) -> None:
    """解析 ChatbotMessage，调用助手，并回复消息。

    Args:
        callback_data: CallbackMessage.data dict（来自 DingTalk SDK）
    """
    import dingtalk_stream

    message = dingtalk_stream.ChatbotMessage.from_dict(callback_data)

    # 提取纯文本，去除 @机器人 前缀
    text = _extract_text(message)
    if not text:
        logger.debug("DingTalk Stream: 收到空消息，忽略")
        return

    # 会话 ID：优先 conversationId → senderId → "unknown"
    conversation_id = (
        getattr(message, "conversation_id", None)
        or getattr(message, "sender_id", None)
        or "unknown"
    )
    session_id = f"dingtalk:{conversation_id}"
    sender_nick = getattr(message, "sender_nick", "用户")
    logger.info(
        f"DingTalk Stream ← session={session_id}  "
        f"from={sender_nick}  text={text[:60]!r}"
    )

    reply_text = await _invoke_assistant(session_id, text)

    session_webhook = getattr(message, "session_webhook", None)
    if session_webhook:
        await _send_reply(session_webhook, reply_text)
    else:
        logger.warning("DingTalk Stream: 消息缺少 session_webhook，无法回复")


# ── 辅助函数 ──────────────────────────────────────────────


def _extract_text(message: Any) -> str:
    """从 ChatbotMessage 中提取纯文本，去除 @机器人名 前缀。"""
    text_obj = getattr(message, "text", None)
    if text_obj is None:
        return ""
    raw: str = getattr(text_obj, "content", "") or ""
    # 去除开头所有 @xxx 标记（群消息中机器人名可能出现在开头）
    return re.sub(r"^(@\S+\s*)+", "", raw.strip()).strip()


async def _invoke_assistant(session_id: str, text: str) -> str:
    """为每条消息创建独立数据库会话并调用 PersonalAssistant.reply()。"""
    from agentpal.agents.personal_assistant import PersonalAssistant
    from agentpal.database import AsyncSessionLocal
    from agentpal.memory.factory import MemoryFactory

    settings = get_settings()
    async with AsyncSessionLocal() as db:
        memory = MemoryFactory.create(settings.memory_backend, db=db)
        assistant = PersonalAssistant(session_id=session_id, memory=memory, db=db)
        return await assistant.reply(text)


async def _send_reply(session_webhook: str, text: str) -> None:
    """通过 DingTalk 临时 sessionWebhook 发送文本回复（异步 HTTP）。"""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                session_webhook,
                json={
                    "msgtype": "text",
                    "text": {"content": text},
                    "at": {"isAtAll": False},
                },
            )
        if resp.status_code != 200:
            logger.warning(f"DingTalk Stream: 回复失败 HTTP {resp.status_code}")
        else:
            logger.debug(f"DingTalk Stream → 回复已发送 ({len(text)} 字符) ✅")
    except Exception as exc:
        logger.error(f"DingTalk Stream: 发送回复异常 — {exc}")


# ── 单例（供 main.py lifespan 调用）──────────────────────
dingtalk_stream_worker = DingTalkStreamWorker()
