"""渠道 Webhook 接入端点。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.channels import DingTalkChannel, FeishuChannel, IMessageChannel
from agentpal.channels.dingtalk_api import is_duplicate_msg
from agentpal.config import get_settings
from agentpal.database import get_db
from agentpal.memory.factory import MemoryFactory
from agentpal.agents.personal_assistant import PersonalAssistant

router = APIRouter()

# 模块级单例，避免每次请求重建
_channels = {
    "dingtalk": DingTalkChannel(),
    "feishu": FeishuChannel(),
    "imessage": IMessageChannel(),
}


async def _handle_incoming(channel_name: str, payload: dict[str, Any], db: AsyncSession, request: Request | None = None) -> dict:
    """公共处理逻辑：解析消息 → 调用助手 → 返回回复。"""
    import re as _re

    ch = _channels.get(channel_name)
    if ch is None:
        raise HTTPException(status_code=400, detail=f"Unknown channel: {channel_name}")

    # 消息去重（DingTalk 可能重试投递）
    msg_id = payload.get("msgId")
    if msg_id and is_duplicate_msg(msg_id):
        return {"status": "duplicate"}

    incoming = await ch.parse_incoming(payload)
    if incoming is None:
        return {"status": "ignored"}

    # ── Tool Guard 确认拦截 ──────────────────────────
    guard_match = _re.match(
        r"^(confirm|cancel|确认|取消)\s+([a-f0-9-]+)$",
        incoming.text.strip(),
        _re.IGNORECASE,
    )
    if guard_match:
        action, request_id = guard_match.groups()
        approved = action.lower() in ("confirm", "确认")
        from agentpal.channels.base import OutgoingMessage

        resolved = False
        zmq_manager = (getattr(request.app.state, "scheduler", None) or getattr(request.app.state, "zmq_manager", None)) if request is not None else None
        if zmq_manager is not None:
            from agentpal.zmq_bus.protocol import Envelope, MessageType

            envelope = Envelope(
                msg_type=MessageType.TOOL_GUARD_RESOLVE,
                source="channel:tool_guard",
                target=f"pa:{incoming.session_id}",
                session_id=incoming.session_id,
                payload={"request_id": request_id, "approved": approved},
            )
            await zmq_manager.send_to_agent(f"pa:{incoming.session_id}", envelope)
            resolved = True
        else:
            from agentpal.tools.tool_guard import ToolGuardManager

            guard = ToolGuardManager.get_instance()
            resolved = guard.resolve(request_id, approved)

        if resolved:
            await ch.send(
                OutgoingMessage(
                    session_id=incoming.session_id,
                    text=f"{'✅ 已确认执行' if approved else '❌ 已取消执行'} [{request_id[:8]}]",
                )
            )
        else:
            await ch.send(
                OutgoingMessage(
                    session_id=incoming.session_id,
                    text=f"⚠️ 未找到确认请求 [{request_id[:8]}]，可能已过期",
                )
            )
        return {"status": "ok"}

    # ── 清空上下文指令 ──────────────────────────────────
    if incoming.text.strip().lower() in ("/clear", "/reset", "清空上下文", "清空记忆"):
        from agentpal.channels.base import OutgoingMessage

        _settings = get_settings()
        memory = MemoryFactory.create(_settings.memory_backend, db=db)
        await memory.clear(incoming.session_id)
        await db.commit()
        await ch.send(
            OutgoingMessage(
                session_id=incoming.session_id,
                text="🧹 上下文已清空，下次对话将从全新状态开始。",
            )
        )
        return {"status": "ok"}

    settings = get_settings()
    memory = MemoryFactory.create(settings.memory_backend, db=db)
    assistant = PersonalAssistant(session_id=incoming.session_id, memory=memory, db=db)
    reply_text = await assistant.reply(incoming.text)

    from agentpal.channels.base import OutgoingMessage
    await ch.send(OutgoingMessage(session_id=incoming.session_id, text=reply_text))
    return {"status": "ok"}


@router.post("/dingtalk/webhook")
async def dingtalk_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """钉钉消息 Webhook。"""
    ch = _channels["dingtalk"]
    body = await request.body()
    headers = dict(request.headers)
    if not await ch.verify_signature(headers, body):
        raise HTTPException(status_code=401, detail="Invalid signature")
    payload = await request.json()
    return await _handle_incoming("dingtalk", payload, db, request)


@router.post("/feishu/webhook")
async def feishu_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """飞书事件订阅 Webhook。"""
    payload = await request.json()
    # 处理飞书 URL 验证挑战
    if "challenge" in payload:
        return {"challenge": payload["challenge"]}
    ch = _channels["feishu"]
    body = await request.body()
    if not await ch.verify_signature(dict(request.headers), body):
        raise HTTPException(status_code=401, detail="Invalid signature")
    return await _handle_incoming("feishu", payload, db, request)
