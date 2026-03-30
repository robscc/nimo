"""DingTalk Stream 模式机器人 Worker。

架构：
    DingTalk 云 --WebSocket--> _SdkChatbotHandler.process()
                                          |
                                  _handle_message()
                                          |
                          SchedulerClient (ZMQ) → PA Worker
                                          |
                      sessionWebhook --HTTP--> DingTalk 云
                      （逐条发送：工具/思考/计划/安全确认 → 最终回复）

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

from agentpal.channels import dingtalk_api
from agentpal.config import get_settings

# 模块级 Scheduler 引用（由 start() 注入）
_scheduler: Any = None


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

    async def start(self, *, scheduler: Any = None) -> None:
        """检查配置并在后台启动 DingTalk Stream 客户端。

        Args:
            scheduler: SchedulerClient 实例，用于通过 ZMQ 路由到 PA Worker。
                       为 None 时回退到直接 PersonalAssistant 调用。
        """
        global _scheduler
        _scheduler = scheduler

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
        await dingtalk_api.close_http_client()
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


# ── 核心消息处理逻辑 ──────────────────────────────────────


async def _handle_message(callback_data: dict[str, Any]) -> None:
    """解析 ChatbotMessage，调用助手，逐条发送回复。

    支持：
        - 普通对话消息 → 路由到 PA Worker
        - Tool Guard 确认/取消 → 直接 resolve
        - 消息去重 → 跳过重复 msgId

    Args:
        callback_data: CallbackMessage.data dict（来自 DingTalk SDK）
    """
    import dingtalk_stream

    message = dingtalk_stream.ChatbotMessage.from_dict(callback_data)

    # 消息去重
    msg_id = getattr(message, "msg_id", None) or callback_data.get("msgId")
    if dingtalk_api.is_duplicate_msg(msg_id):
        logger.debug(f"DingTalk Stream: 重复消息 msgId={msg_id}，跳过")
        return

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

    # ── Tool Guard 确认拦截 ──────────────────────────
    guard_match = re.match(
        r"^(confirm|cancel|确认|取消)\s+([a-f0-9-]+)$",
        text.strip(),
        re.IGNORECASE,
    )
    if guard_match:
        from agentpal.tools.tool_guard import ToolGuardManager

        action, request_id = guard_match.groups()
        approved = action.lower() in ("confirm", "确认")
        guard = ToolGuardManager.get_instance()
        if guard.resolve(request_id, approved):
            reply = f"{'✅ 已确认执行' if approved else '❌ 已取消执行'} [{request_id[:8]}]"
        else:
            reply = f"⚠️ 未找到确认请求 [{request_id[:8]}]，可能已过期"
        await dingtalk_api.send_text(session_webhook, reply)
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
    """消费事件流，逐条通过 sessionWebhook 发送消息。

    优先通过 SchedulerClient (ZMQ) 路由到 PA Worker；
    Scheduler 不可用时回退到直接 PersonalAssistant 调用。
    """
    from agentpal.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await _ensure_dingtalk_session(db, session_id)

    if _scheduler is not None:
        await _stream_reply_via_scheduler(session_id, text, session_webhook)
    else:
        await _stream_reply_direct(session_id, text, session_webhook)


async def _stream_reply_via_scheduler(
    session_id: str, text: str, session_webhook: str
) -> None:
    """通过 SchedulerClient (ZMQ) 路由到 PA Worker 处理对话。"""
    import uuid

    from agentpal.zmq_bus.protocol import Envelope, MessageType

    msg_id = str(uuid.uuid4())

    # 1. 确保 PA daemon 运行
    await _scheduler.ensure_pa_daemon(session_id)

    # 2. 创建事件订阅者
    subscriber = _scheduler.create_event_subscriber(
        topic=f"session:{session_id}",
        filter_msg_id=msg_id,
    )

    # 3. 发送 CHAT_REQUEST
    envelope = Envelope(
        msg_id=msg_id,
        msg_type=MessageType.CHAT_REQUEST,
        source="dingtalk:stream",
        target=f"pa:{session_id}",
        session_id=session_id,
        payload={
            "message": text,
            "channel": "dingtalk",
        },
    )
    await _scheduler.send_to_agent(f"pa:{session_id}", envelope)

    # 4. 订阅事件 → 逐条发送到钉钉
    reply_parts: list[str] = []
    settings = get_settings()

    try:
        async with subscriber:
            async for event in subscriber:
                event_clean = {k: v for k, v in event.items() if not k.startswith("_")}
                await _dispatch_event(event_clean, session_webhook, reply_parts, settings)

                etype = event_clean.get("type", "")
                if etype in ("done", "error"):
                    break
    except Exception as exc:
        logger.error(f"DingTalk Stream: Scheduler 事件流异常 — {exc}")
        await dingtalk_api.send_text(session_webhook, f"❌ 内部错误: {exc}")
        return

    # 发送最终文本回复
    final_text = "".join(reply_parts)
    if final_text.strip():
        await dingtalk_api.send_text(session_webhook, final_text)


async def _stream_reply_direct(
    session_id: str, text: str, session_webhook: str
) -> None:
    """直接调用 PersonalAssistant（Scheduler 不可用时的回退路径）。"""
    from agentpal.agents.personal_assistant import PersonalAssistant
    from agentpal.database import AsyncSessionLocal
    from agentpal.memory.factory import MemoryFactory

    settings = get_settings()
    reply_parts: list[str] = []

    async with AsyncSessionLocal() as db:
        memory = MemoryFactory.create(settings.memory_backend, db=db)
        assistant = PersonalAssistant(session_id=session_id, memory=memory, db=db)

        async for event in assistant.reply_stream(text):
            await _dispatch_event(event, session_webhook, reply_parts, settings)

    # 发送最终文本回复
    final_text = "".join(reply_parts)
    if final_text.strip():
        await dingtalk_api.send_text(session_webhook, final_text)


# ── 事件分发（统一处理所有 SSE 事件类型）──────────────────


async def _dispatch_event(
    event: dict[str, Any],
    session_webhook: str,
    reply_parts: list[str],
    settings: Any,
) -> None:
    """将单个 SSE 事件转化为钉钉消息发送。"""
    etype = event.get("type", "")

    if etype == "thinking_delta":
        # thinking 不逐条发送，避免刷屏（收集后可选发送摘要）
        pass

    elif etype == "tool_start":
        tc_name = event.get("name", "")
        tc_input = event.get("input", {})
        md = dingtalk_api.format_tool_start(tc_name, tc_input)
        await dingtalk_api.send_markdown(session_webhook, "⏳ 工具调用", md)

    elif etype == "tool_done":
        md = dingtalk_api.format_tool_done(event)
        await dingtalk_api.send_markdown(session_webhook, "✅ 工具结果", md)

        # 如果是 send_file_to_user 且成功，尝试发送图片
        if event.get("name") == "send_file_to_user" and not event.get("error"):
            await _try_send_image(session_webhook, event, settings)

    elif etype == "tool_guard_request":
        md = dingtalk_api.format_tool_guard_request(event)
        await dingtalk_api.send_markdown(session_webhook, "🔒 安全确认", md)

    elif etype == "tool_guard_resolved":
        approved = event.get("approved", False)
        status = "✅ 已确认执行" if approved else "❌ 已取消"
        await dingtalk_api.send_markdown(session_webhook, "🔒 安全确认", status)

    elif etype == "retry":
        attempt = event.get("attempt", 0)
        max_attempts = event.get("max_attempts", 0)
        error = event.get("error", "")
        md = f"⏳ 第 {attempt}/{max_attempts} 次重试…\n\n> {error}"
        await dingtalk_api.send_markdown(session_webhook, "⏳ 重试", md)

    elif etype == "plan_generating":
        await dingtalk_api.send_markdown(
            session_webhook, "📋 计划", "正在生成执行计划…"
        )

    elif etype == "plan_ready":
        plan = event.get("plan", {})
        md = dingtalk_api.format_plan_ready(plan)
        await dingtalk_api.send_markdown(session_webhook, "📋 执行计划", md)

    elif etype == "plan_step_start":
        step = event.get("step", {})
        idx = event.get("step_index", 0) + 1
        total = event.get("total_steps", "?")
        title = step.get("title", "")
        md = f"▶️ 步骤 {idx}/{total}: **{title}**"
        await dingtalk_api.send_markdown(session_webhook, "📋 步骤", md)

    elif etype == "plan_step_done":
        idx = event.get("step_index", 0) + 1
        result = event.get("result", "")
        status = "❌" if result.startswith("失败") else "✅"
        md = f"{status} 步骤 {idx} 完成"
        if result:
            md += f"\n\n> {result[:200]}"
        await dingtalk_api.send_markdown(session_webhook, "📋 步骤", md)

    elif etype == "plan_completed":
        await dingtalk_api.send_markdown(
            session_webhook, "📋 计划", "执行计划已完成 ✅"
        )

    elif etype == "plan_cancelled":
        await dingtalk_api.send_markdown(
            session_webhook, "📋 计划", "执行计划已取消"
        )

    elif etype == "text_delta":
        reply_parts.append(event.get("delta", ""))

    elif etype == "error":
        err_msg = event.get("message", "未知错误")
        await dingtalk_api.send_text(session_webhook, f"❌ {err_msg}")

    elif etype == "file":
        # 文件事件（来自 pa_daemon 的 send_file_to_user 处理）
        url = event.get("url", "")
        name = event.get("name", "文件")
        mime = event.get("mime", "")
        if mime and mime.startswith("image/"):
            md = f"📷 **{name}**\n\n![{name}]({url})"
            await dingtalk_api.send_markdown(session_webhook, "📷 图片", md)
        elif url:
            await dingtalk_api.send_text(session_webhook, f"📎 文件: {name} — {url}")

    # done / heartbeat / tool_guard_waiting → 不发送


# ── 图片发送 ──────────────────────────────────────────────


async def _try_send_image(
    session_webhook: str, tool_done_event: dict[str, Any], settings: Any
) -> None:
    """尝试从 send_file_to_user 的输出中提取文件并发送图片到钉钉。"""
    try:
        output_str = tool_done_event.get("output", "")
        info = json.loads(output_str)
        if info.get("status") != "sent":
            return

        filename = info.get("filename", "")

        # 检查是否为图片
        mime, _ = mimetypes.guess_type(filename)
        if not mime or not mime.startswith("image/"):
            logger.debug(f"DingTalk Stream: 文件 {filename} 非图片类型（{mime}），跳过")
            return

        # 定位本地文件
        local_path = Path("uploads") / filename
        if not local_path.exists():
            logger.warning(f"DingTalk Stream: 图片文件不存在 {local_path}")
            return

        # 文件头校验
        _IMAGE_MAGIC = {
            b"\x89PNG": "image/png",
            b"\xff\xd8\xff": "image/jpeg",
            b"GIF8": "image/gif",
            b"RIFF": "image/webp",
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

        media_id = await dingtalk_api.upload_media(app_key, app_secret, local_path, mime)
        if not media_id:
            return

        md = f"![{filename}]({media_id})"
        await dingtalk_api.send_markdown(session_webhook, "📷 图片", md)
        logger.info(f"DingTalk Stream → 图片已发送: {filename}")

    except Exception as exc:
        logger.warning(f"DingTalk Stream: 发送图片失败 — {exc}")


# ── 文本提取 ──────────────────────────────────────────────


def _extract_text(message: Any) -> str:
    """从 ChatbotMessage 中提取纯文本，去除 @机器人名 前缀。"""
    text_obj = getattr(message, "text", None)
    if text_obj is None:
        return ""
    raw: str = getattr(text_obj, "content", "") or ""
    # 去除开头所有 @xxx 标记（群消息中机器人名可能出现在开头）
    stripped = re.sub(r"^(@\S+\s*)+", "", raw.strip()).strip()
    # 如果剥离后为空，使用原始文本（避免过度剥离）
    return stripped or raw.strip()


# ── 兼容旧接口（供测试使用）──────────────────────────────

# 保留旧函数名作为别名，避免破坏现有测试
_send_reply = dingtalk_api.send_text
_send_markdown_reply = dingtalk_api.send_markdown
_format_tool_start = dingtalk_api.format_tool_start
_format_tool_done = dingtalk_api.format_tool_done
_get_dingtalk_access_token = dingtalk_api.get_access_token
_upload_to_dingtalk = dingtalk_api.upload_media


# ── 单例（供 main.py lifespan 调用）──────────────────────
dingtalk_stream_worker = DingTalkStreamWorker()
