"""SkillEventBus — 全局技能热重载 SSE 事件总线。

前端通过 GET /api/v1/skills/events 订阅，
后端安装/回滚技能后调用 broadcast() 推送事件。

使用示例：
    # 广播事件
    from agentpal.services.skill_event_bus import skill_event_bus
    await skill_event_bus.broadcast({
        "type": "skill_reloaded",
        "name": "my-skill",
        "version": "1.2.0",
        "action": "install",   # install | rollback | uninstall
    })

    # SSE 端点订阅
    async def sse_endpoint():
        queue = skill_event_bus.subscribe()
        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=30)
                yield f"data: {json.dumps(event)}\\n\\n"
        except asyncio.TimeoutError:
            yield "data: {\"type\": \"ping\"}\\n\\n"  # keepalive
        finally:
            skill_event_bus.unsubscribe(queue)
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger


class SkillEventBus:
    """Skill 热重载 SSE 事件总线，支持多客户端并发订阅。

    线程安全：仅在 asyncio 事件循环中使用，无需额外锁。
    """

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[dict[str, Any]]] = []

    # ── 订阅管理 ────────────────────────────────────────────

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """注册新订阅者，返回专属消息队列。"""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=64)
        self._subscribers.append(q)
        logger.debug(f"SkillEventBus: 新增订阅者，当前共 {len(self._subscribers)} 个")
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        """注销订阅者。"""
        try:
            self._subscribers.remove(q)
            logger.debug(f"SkillEventBus: 订阅者离开，当前共 {len(self._subscribers)} 个")
        except ValueError:
            pass

    @property
    def subscriber_count(self) -> int:
        """当前在线订阅者数量。"""
        return len(self._subscribers)

    # ── 广播 ────────────────────────────────────────────────

    async def broadcast(self, event: dict[str, Any]) -> None:
        """向所有订阅者广播事件。

        如果某个订阅者队列已满（maxsize=64），跳过以避免阻塞。
        """
        if not self._subscribers:
            return

        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("SkillEventBus: 订阅者队列已满，跳过该客户端")

        logger.debug(
            f"SkillEventBus: 广播事件 type={event.get('type')!r} → {len(self._subscribers)} 个订阅者"
        )


# 全局单例
skill_event_bus = SkillEventBus()
