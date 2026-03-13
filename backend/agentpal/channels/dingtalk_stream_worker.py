"""DingTalk Stream 模式机器人 Worker。

架构：
    DingTalk 云 --WebSocket--> _SdkChatbotHandler.process()
                                          |
                                  _handle_message()
                                          |
                              PersonalAssistant.reply_stream()
                                          |
                      sessionWebhook --HTTP--> DingTalk 云
                      （逐条发送：工具开始 → 工具完成 → 图片 → 最终回复）

配置（.env 或 ~/.nimo/config.yaml）：
    DINGTALK_ENABLED=true
    DINGTALK_APP_KEY=<ClientID / AppKey>
    DINGTALK_APP_SECRET=<ClientSecret / AppSecret>

注意：Stream 模式无需公网 IP，服务方主动连接钉钉云，适合本地/内网部署。
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path
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

    async def _watch_connection(c: Any) -> None:
        """轮询 client.websocket，首次建连后打印成功日志（SDK 无连接回调）。"""
        for _ in range(150):  # 最长等 30 秒（每次 200 ms）
            await asyncio.sleep(0.2)
            if getattr(c, "websocket", None) is not None:
                logger.info("DingTalk Stream: WebSocket 连接成功 ✅，等待消息中…")
                return
        logger.warning("DingTalk Stream: 等待 WebSocket 建连超时（30s），请检查网络配置")

    credential = dingtalk_stream.Credential(app_key, app_secret)
    client = dingtalk_stream.DingTalkStreamClient(credential)
    client.register_callback_handler(
        dingtalk_stream.ChatbotMessage.TOPIC,
        _SdkChatbotHandler(),
    )
    asyncio.create_task(_watch_connection(client))
    logger.info("DingTalk Stream: 正在连接…")
    await client.start()  # SDK 内部 while True 处理重连


# ── 核心消息处理逻辑（纯函数，方便单元测试）─────────────────


async def _handle_message(callback_data: dict[str, Any]) -> None:
    """解析 ChatbotMessage，调用助手，逐条发送回复。

    流程：
        1. tool_start  → 立即发一条 Markdown "⏳ 正在调用 xxx …"
        2. tool_done   → 立即发一条 Markdown 展示工具结果
        3. file (图片) → 上传到钉钉获取 mediaId，发 Markdown 内嵌图片
        4. 最终文本    → 纯文本 / Markdown 回复

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

    session_webhook = getattr(message, "session_webhook", None)
    if not session_webhook:
        logger.warning("DingTalk Stream: 消息缺少 session_webhook，无法回复")
        return

    await _stream_reply(session_id, text, session_webhook)


# ── 流式回复：逐条发送 ────────────────────────────────────


async def _ensure_dingtalk_session(db: Any, session_id: str) -> None:
    """Upsert SessionRecord，确保钉钉会话出现在会话列表中。"""
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    from agentpal.models.session import SessionRecord

    now = datetime.now(timezone.utc)
    stmt = (
        sqlite_insert(SessionRecord)
        .values(
            id=session_id,
            channel="dingtalk",
            status="active",
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["id"],
            set_={"updated_at": now},
        )
    )
    await db.execute(stmt)
    await db.commit()


async def _stream_reply(
    session_id: str, text: str, session_webhook: str
) -> None:
    """消费 reply_stream() 事件，逐条通过 sessionWebhook 发送消息。"""
    from agentpal.agents.personal_assistant import PersonalAssistant
    from agentpal.database import AsyncSessionLocal
    from agentpal.memory.factory import MemoryFactory

    settings = get_settings()
    reply_parts: list[str] = []

    async with AsyncSessionLocal() as db:
        await _ensure_dingtalk_session(db, session_id)
        memory = MemoryFactory.create(settings.memory_backend, db=db)
        assistant = PersonalAssistant(session_id=session_id, memory=memory, db=db)

        async for event in assistant.reply_stream(text):
            etype = event.get("type")

            if etype == "tool_start":
                # 立即发送 "正在调用" 提示
                tc_name = event.get("name", "")
                tc_input = event.get("input", {})
                md = _format_tool_start(tc_name, tc_input)
                await _send_markdown_reply(session_webhook, "⏳ 工具调用", md)

            elif etype == "tool_done":
                # 立即发送工具结果
                md = _format_tool_done(event)
                await _send_markdown_reply(session_webhook, "✅ 工具结果", md)

                # 如果是 send_file_to_user 且成功，尝试发送图片
                if (
                    event.get("name") == "send_file_to_user"
                    and not event.get("error")
                ):
                    await _try_send_image(session_webhook, event, settings)

            elif etype == "text_delta":
                reply_parts.append(event.get("delta", ""))

            elif etype == "error":
                err_msg = event.get("message", "未知错误")
                await _send_reply(session_webhook, f"❌ {err_msg}")

    # 发送最终文本回复
    final_text = "".join(reply_parts)
    if final_text.strip():
        await _send_reply(session_webhook, final_text)


# ── 工具消息格式化 ────────────────────────────────────────


def _format_tool_start(name: str, tc_input: Any) -> str:
    """格式化 tool_start 事件为钉钉 Markdown。"""
    if isinstance(tc_input, dict):
        input_str = json.dumps(tc_input, ensure_ascii=False, separators=(",", ":"))
    else:
        input_str = str(tc_input)

    if len(input_str) > 200:
        input_str = input_str[:200] + "…"

    return f"⏳ 正在调用 **{name}** …\n\n> 输入：{input_str}"


def _format_tool_done(event: dict[str, Any]) -> str:
    """格式化 tool_done 事件为钉钉 Markdown。"""
    name = event.get("name", "unknown")
    output = event.get("output", "")
    error = event.get("error")
    duration = event.get("duration_ms")

    output_str = str(output)
    if len(output_str) > 500:
        output_str = output_str[:500] + "…"

    section = f"🔧 **{name}**\n"
    if error:
        section += f"> ❌ 错误：{error}\n"
    else:
        section += f"> 输出：{output_str}\n"
    if duration is not None:
        section += f"> ⏱ {duration}ms"

    return section


# ── 图片发送 ──────────────────────────────────────────────


async def _try_send_image(
    session_webhook: str, tool_done_event: dict[str, Any], settings: Any
) -> None:
    """尝试从 send_file_to_user 的输出中提取文件并发送图片到钉钉。

    策略：
        1. 解析 tool output 获取本地文件路径
        2. 上传到钉钉 /media/upload 获取 mediaId
        3. 通过 Markdown `![](mediaId)` 发送图片
    """
    try:
        output_str = tool_done_event.get("output", "")
        info = json.loads(output_str)
        if info.get("status") != "sent":
            return

        filename = info.get("filename", "")

        # 检查是否为图片（文件名 + 文件头双重验证）
        mime, _ = mimetypes.guess_type(filename)
        if not mime or not mime.startswith("image/"):
            logger.debug(f"DingTalk Stream: 文件 {filename} 非图片类型（{mime}），跳过")
            return

        # 定位本地文件
        local_path = Path("uploads") / filename
        if not local_path.exists():
            logger.warning(f"DingTalk Stream: 图片文件不存在 {local_path}")
            return

        # 文件头校验：确认实际内容是图片
        _IMAGE_MAGIC = {
            b"\x89PNG": "image/png",
            b"\xff\xd8\xff": "image/jpeg",
            b"GIF8": "image/gif",
            b"RIFF": "image/webp",  # RIFF....WEBP
            b"BM": "image/bmp",
        }
        with open(local_path, "rb") as f:
            header = f.read(8)
        if not any(header.startswith(magic) for magic in _IMAGE_MAGIC):
            logger.debug(
                f"DingTalk Stream: 文件 {filename} 文件头不匹配已知图片格式，跳过"
            )
            return

        # 上传到钉钉获取 mediaId
        app_key = settings.dingtalk_app_key
        app_secret = settings.dingtalk_app_secret
        if not app_key or not app_secret:
            logger.debug("DingTalk Stream: 缺少 app_key/secret，跳过图片上传")
            return

        media_id = await _upload_to_dingtalk(app_key, app_secret, local_path, mime)
        if not media_id:
            return

        # 用 Markdown 发送图片
        md = f"![{filename}]({media_id})"
        await _send_markdown_reply(session_webhook, "📷 图片", md)
        logger.info(f"DingTalk Stream → 图片已发送: {filename}")

    except Exception as exc:
        logger.warning(f"DingTalk Stream: 发送图片失败 — {exc}")


async def _get_dingtalk_access_token(app_key: str, app_secret: str) -> str | None:
    """获取钉钉 API access_token。"""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://oapi.dingtalk.com/gettoken",
                params={"appkey": app_key, "appsecret": app_secret},
            )
        data = resp.json()
        if data.get("errcode") == 0:
            return data["access_token"]
        logger.warning(f"DingTalk Stream: 获取 access_token 失败 — {data}")
        return None
    except Exception as exc:
        logger.warning(f"DingTalk Stream: 获取 access_token 异常 — {exc}")
        return None


async def _upload_to_dingtalk(
    app_key: str, app_secret: str, file_path: Path, mime_type: str
) -> str | None:
    """上传文件到钉钉媒体接口，返回 mediaId。"""
    import httpx

    access_token = await _get_dingtalk_access_token(app_key, app_secret)
    if not access_token:
        return None

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            with open(file_path, "rb") as f:
                resp = await client.post(
                    "https://oapi.dingtalk.com/media/upload",
                    params={"access_token": access_token, "type": "image"},
                    files={"media": (file_path.name, f, mime_type)},
                )
        data = resp.json()
        if data.get("errcode") == 0:
            media_id = data.get("media_id", "")
            logger.debug(f"DingTalk Stream: 图片上传成功 media_id={media_id[:20]}…")
            return media_id
        logger.warning(f"DingTalk Stream: 图片上传失败 — {data}")
        return None
    except Exception as exc:
        logger.warning(f"DingTalk Stream: 图片上传异常 — {exc}")
        return None


# ── 基础发送函数 ──────────────────────────────────────────


def _extract_text(message: Any) -> str:
    """从 ChatbotMessage 中提取纯文本，去除 @机器人名 前缀。"""
    text_obj = getattr(message, "text", None)
    if text_obj is None:
        return ""
    raw: str = getattr(text_obj, "content", "") or ""
    # 去除开头所有 @xxx 标记（群消息中机器人名可能出现在开头）
    return re.sub(r"^(@\S+\s*)+", "", raw.strip()).strip()


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


async def _send_markdown_reply(
    session_webhook: str, title: str, markdown_text: str
) -> None:
    """通过 DingTalk 临时 sessionWebhook 发送 Markdown 回复（异步 HTTP）。"""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                session_webhook,
                json={
                    "msgtype": "markdown",
                    "markdown": {
                        "title": title,
                        "text": markdown_text,
                    },
                    "at": {"isAtAll": False},
                },
            )
        if resp.status_code != 200:
            logger.warning(f"DingTalk Stream: Markdown 回复失败 HTTP {resp.status_code}")
        else:
            logger.debug(
                f"DingTalk Stream → Markdown 回复已发送 ({len(markdown_text)} 字符) ✅"
            )
    except Exception as exc:
        logger.error(f"DingTalk Stream: 发送 Markdown 回复异常 — {exc}")


# ── 单例（供 main.py lifespan 调用）──────────────────────
dingtalk_stream_worker = DingTalkStreamWorker()
