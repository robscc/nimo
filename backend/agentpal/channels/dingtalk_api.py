"""DingTalk 共享 API 工具 — httpx 客户端复用 + access_token 缓存 + 消息发送。

被 dingtalk.py（Webhook 模式）和 dingtalk_stream_worker.py（Stream 模式）共同引用。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

# ── 共享 httpx 客户端 ──────────────────────────────────────

_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """获取模块级共享 httpx.AsyncClient（懒初始化）。"""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=30)
    return _http_client


async def close_http_client() -> None:
    """关闭共享客户端（在 DingTalkStreamWorker.stop() 中调用）。"""
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None


# ── access_token 缓存 ─────────────────────────────────────

_token_cache: dict[str, Any] = {}  # {"token": str, "expires_at": float}
_token_lock = asyncio.Lock()


async def get_access_token(app_key: str, app_secret: str) -> str | None:
    """获取钉钉 API access_token（带缓存，有效期 7200s，提前 300s 刷新）。"""
    now = time.time()
    cached = _token_cache.get("token")
    expires_at = _token_cache.get("expires_at", 0)
    if cached and now < expires_at:
        return cached

    async with _token_lock:
        # double-check after acquiring lock
        cached = _token_cache.get("token")
        expires_at = _token_cache.get("expires_at", 0)
        if cached and now < expires_at:
            return cached

        try:
            client = get_http_client()
            resp = await client.get(
                "https://oapi.dingtalk.com/gettoken",
                params={"appkey": app_key, "appsecret": app_secret},
            )
            data = resp.json()
            if data.get("errcode") == 0:
                token = data["access_token"]
                expires_in = data.get("expires_in", 7200)
                _token_cache["token"] = token
                _token_cache["expires_at"] = now + expires_in - 300
                return token
            logger.warning(f"DingTalk: 获取 access_token 失败 — {data}")
            return None
        except Exception as exc:
            logger.warning(f"DingTalk: 获取 access_token 异常 — {exc}")
            return None


# ── 消息发送 ───────────────────────────────────────────────


async def send_text(webhook_url: str, text: str) -> None:
    """通过 webhook URL 发送文本消息。"""
    try:
        client = get_http_client()
        resp = await client.post(
            webhook_url,
            json={
                "msgtype": "text",
                "text": {"content": text},
                "at": {"isAtAll": False},
            },
        )
        if resp.status_code != 200:
            logger.warning(f"DingTalk: 文本回复失败 HTTP {resp.status_code}")
        else:
            logger.debug(f"DingTalk → 文本回复已发送 ({len(text)} 字符)")
    except Exception as exc:
        logger.error(f"DingTalk: 发送文本回复异常 — {exc}")


async def send_markdown(webhook_url: str, title: str, markdown_text: str) -> None:
    """通过 webhook URL 发送 Markdown 消息。"""
    try:
        client = get_http_client()
        resp = await client.post(
            webhook_url,
            json={
                "msgtype": "markdown",
                "markdown": {"title": title, "text": markdown_text},
                "at": {"isAtAll": False},
            },
        )
        if resp.status_code != 200:
            logger.warning(f"DingTalk: Markdown 回复失败 HTTP {resp.status_code}")
        else:
            logger.debug(f"DingTalk → Markdown 回复已发送 ({len(markdown_text)} 字符)")
    except Exception as exc:
        logger.error(f"DingTalk: 发送 Markdown 回复异常 — {exc}")


async def send_action_card(
    webhook_url: str,
    title: str,
    text: str,
    *,
    btns: list[dict[str, str]] | None = None,
    single_title: str | None = None,
    single_url: str | None = None,
) -> None:
    """通过 webhook URL 发送 ActionCard 消息。"""
    card: dict[str, Any] = {"title": title, "text": text}
    if btns:
        card["btnOrientation"] = "1"
        card["btns"] = btns
    elif single_title and single_url:
        card["singleTitle"] = single_title
        card["singleURL"] = single_url

    try:
        client = get_http_client()
        resp = await client.post(
            webhook_url,
            json={"msgtype": "actionCard", "actionCard": card},
        )
        if resp.status_code != 200:
            logger.warning(f"DingTalk: ActionCard 回复失败 HTTP {resp.status_code}")
        else:
            logger.debug(f"DingTalk → ActionCard 回复已发送: {title}")
    except Exception as exc:
        logger.error(f"DingTalk: 发送 ActionCard 异常 — {exc}")


# ── 媒体上传 ───────────────────────────────────────────────


async def upload_media(
    app_key: str, app_secret: str, file_path: Path, mime_type: str
) -> str | None:
    """上传文件到钉钉媒体接口，返回 mediaId。"""
    access_token = await get_access_token(app_key, app_secret)
    if not access_token:
        return None

    try:
        client = get_http_client()
        with open(file_path, "rb") as f:
            resp = await client.post(
                "https://oapi.dingtalk.com/media/upload",
                params={"access_token": access_token, "type": "image"},
                files={"media": (file_path.name, f, mime_type)},
            )
        data = resp.json()
        if data.get("errcode") == 0:
            media_id = data.get("media_id", "")
            logger.debug(f"DingTalk: 图片上传成功 media_id={media_id[:20]}…")
            return media_id
        logger.warning(f"DingTalk: 图片上传失败 — {data}")
        return None
    except Exception as exc:
        logger.warning(f"DingTalk: 图片上传异常 — {exc}")
        return None


# ── Markdown 转义 ──────────────────────────────────────────


def escape_markdown(text: str) -> str:
    """转义 DingTalk Markdown 特殊字符。"""
    for ch in ("*", "_", "[", "]", "(", ")", ">", "#", "`"):
        text = text.replace(ch, f"\\{ch}")
    return text


# ── 消息去重 ───────────────────────────────────────────────

_seen_msg_ids: dict[str, float] = {}  # msg_id → timestamp
_DEDUP_MAX = 1000


def is_duplicate_msg(msg_id: str | None) -> bool:
    """检查消息是否重复（LRU，上限 1000 条）。返回 True 表示重复应跳过。"""
    if not msg_id:
        return False
    if msg_id in _seen_msg_ids:
        return True
    # evict oldest if full
    if len(_seen_msg_ids) >= _DEDUP_MAX:
        oldest = min(_seen_msg_ids, key=_seen_msg_ids.get)  # type: ignore[arg-type]
        del _seen_msg_ids[oldest]
    _seen_msg_ids[msg_id] = time.time()
    return False


# ── 工具消息格式化 ─────────────────────────────────────────


def format_tool_start(name: str, tc_input: Any) -> str:
    """格式化 tool_start 事件为钉钉 Markdown。"""
    if isinstance(tc_input, dict):
        input_str = json.dumps(tc_input, ensure_ascii=False, separators=(",", ":"))
    else:
        input_str = str(tc_input)

    if len(input_str) > 200:
        input_str = input_str[:200] + "…"

    return f"⏳ 正在调用 **{name}** …\n\n> 输入：{escape_markdown(input_str)}"


def format_tool_done(event: dict[str, Any]) -> str:
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
        section += f"> ❌ 错误：{escape_markdown(str(error))}\n"
    else:
        section += f"> 输出：{escape_markdown(output_str)}\n"
    if duration is not None:
        section += f"> ⏱ {duration}ms"

    return section


def format_tool_guard_request(event: dict[str, Any]) -> str:
    """格式化 tool_guard_request 事件为钉钉 Markdown（方案 B：文本回复确认）。"""
    tool_name = event.get("tool_name", "unknown")
    tool_input = event.get("tool_input", {})
    level = event.get("level", "?")
    description = event.get("description", "")
    request_id = event.get("request_id", "")

    input_str = json.dumps(tool_input, ensure_ascii=False, separators=(",", ":")) if isinstance(tool_input, dict) else str(tool_input)
    if len(input_str) > 200:
        input_str = input_str[:200] + "…"

    short_id = request_id[:8] if request_id else "?"
    return (
        f"🔒 **工具安全确认**\n\n"
        f"**工具**: {tool_name}\n\n"
        f"**输入**: {escape_markdown(input_str)}\n\n"
        f"**安全等级**: {level}\n\n"
        f"**说明**: {description}\n\n"
        f"---\n\n"
        f"请回复以下指令：\n\n"
        f"- `确认 {short_id}` — 允许执行\n"
        f"- `取消 {short_id}` — 拒绝执行"
    )


def format_plan_ready(plan: dict[str, Any]) -> str:
    """格式化 plan_ready 事件为钉钉 Markdown。"""
    goal = plan.get("goal", "")
    summary = plan.get("summary", "")
    steps = plan.get("steps", [])

    lines = [f"📋 **执行计划**\n"]
    if goal:
        lines.append(f"**目标**: {goal}\n")
    if summary:
        lines.append(f"> {summary}\n")
    if steps:
        lines.append("**步骤**:\n")
        for i, step in enumerate(steps, 1):
            title = step.get("title", f"步骤 {i}")
            lines.append(f"{i}. {title}")

    return "\n".join(lines)
