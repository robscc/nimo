"""渠道 Webhook 接入端点。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.channels import DingTalkChannel, FeishuChannel, IMessageChannel
from agentpal.config import get_settings
from agentpal.database import get_db
from agentpal.memory.factory import MemoryFactory
from agentpal.agents.personal_assistant import PersonalAssistant

router = APIRouter()


async def _handle_incoming(channel_name: str, payload: dict[str, Any], db: AsyncSession) -> dict:
    """公共处理逻辑：解析消息 → 调用助手 → 返回回复。"""
    channels = {
        "dingtalk": DingTalkChannel(),
        "feishu": FeishuChannel(),
        "imessage": IMessageChannel(),
    }
    ch = channels.get(channel_name)
    if ch is None:
        raise HTTPException(status_code=400, detail=f"Unknown channel: {channel_name}")

    incoming = await ch.parse_incoming(payload)
    if incoming is None:
        return {"status": "ignored"}

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
    ch = DingTalkChannel()
    body = await request.body()
    headers = dict(request.headers)
    if not await ch.verify_signature(headers, body):
        raise HTTPException(status_code=401, detail="Invalid signature")
    payload = await request.json()
    return await _handle_incoming("dingtalk", payload, db)


@router.post("/feishu/webhook")
async def feishu_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """飞书事件订阅 Webhook。"""
    payload = await request.json()
    # 处理飞书 URL 验证挑战
    if "challenge" in payload:
        return {"challenge": payload["challenge"]}
    ch = FeishuChannel()
    body = await request.body()
    if not await ch.verify_signature(dict(request.headers), body):
        raise HTTPException(status_code=401, detail="Invalid signature")
    return await _handle_incoming("feishu", payload, db)
