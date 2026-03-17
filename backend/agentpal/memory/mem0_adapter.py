"""Mem0Memory — 基于 mem0 的记忆后端适配器。

通过适配 mem0 (https://github.com/mem0ai/mem0) 的 AsyncMemory API，
实现 BaseMemory 接口，支持语义检索和跨 session 搜索。

使用前需安装: pip install mem0ai

配置示例（~/.nimo/config.yaml）：
    memory:
      backend: mem0
      mem0:
        vector_store:
          provider: qdrant
          config:
            host: localhost
            port: 6333
        llm:
          provider: openai
          config:
            model: gpt-4.1-mini
        embedder:
          provider: openai
          config:
            model: text-embedding-3-small
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from agentpal.memory.base import BaseMemory, MemoryMessage, MemoryRole, MemoryScope


class Mem0Memory(BaseMemory):
    """mem0 记忆后端适配器。

    将 AgentPal 的 BaseMemory 接口映射到 mem0 的 AsyncMemory API。

    Scoping 映射：
    - session_id → mem0 run_id
    - user_id    → mem0 user_id
    - channel    → mem0 agent_id（渠道作为 agent 维度）

    Args:
        mem0_config: mem0 配置字典（传给 AsyncMemory.from_config）
        infer:       是否启用 LLM 自动事实提取（默认 False，保持原始消息）
    """

    def __init__(
        self,
        mem0_config: dict[str, Any] | None = None,
        infer: bool = False,
    ) -> None:
        self._config = mem0_config
        self._infer = infer
        self._client: Any = None

    async def _get_client(self) -> Any:
        """延迟初始化 mem0 AsyncMemory 客户端。"""
        if self._client is None:
            try:
                from mem0 import AsyncMemory
            except ImportError as exc:
                raise ImportError(
                    "mem0 未安装。请执行: pip install mem0ai"
                ) from exc

            if self._config:
                self._client = AsyncMemory.from_config(self._config)
            else:
                self._client = AsyncMemory()
        return self._client

    # ── BaseMemory 实现 ───────────────────────────────────

    async def add(self, message: MemoryMessage) -> MemoryMessage:
        """写入消息到 mem0。

        使用 mem0 的 add() API，根据 infer 参数决定是否自动提取事实。
        """
        client = await self._get_client()

        messages = [{"role": str(message.role), "content": message.content}]
        mem0_kwargs: dict[str, Any] = {
            "run_id": message.session_id,
            "infer": self._infer,
        }
        if message.user_id:
            mem0_kwargs["user_id"] = message.user_id
        if message.channel:
            mem0_kwargs["agent_id"] = message.channel
        if message.metadata:
            mem0_kwargs["metadata"] = message.metadata

        result = await client.add(messages, **mem0_kwargs)

        # 提取 mem0 返回的 ID
        if isinstance(result, dict) and "results" in result:
            results = result["results"]
            if results and isinstance(results, list) and len(results) > 0:
                first = results[0]
                if isinstance(first, dict):
                    message.id = first.get("id", message.id or str(uuid.uuid4()))

        if message.id is None:
            message.id = str(uuid.uuid4())

        return message

    async def get_recent(self, session_id: str, limit: int = 20) -> list[MemoryMessage]:
        """获取指定 session 的最近记忆。

        mem0 的 get_all() 不支持分页排序，这里取全部后在客户端截取。
        """
        client = await self._get_client()
        try:
            result = await client.get_all(run_id=session_id)
        except Exception:
            return []

        memories = result.get("results", []) if isinstance(result, dict) else []
        msgs = [_mem0_to_msg(m, session_id) for m in memories]
        # 按时间排序
        msgs.sort(key=lambda m: m.created_at)
        return msgs[-limit:]

    async def clear(self, session_id: str) -> None:
        """清空指定 session 的全部记忆。"""
        client = await self._get_client()
        try:
            await client.delete_all(run_id=session_id)
        except Exception:
            pass

    async def search(
        self,
        session_id: str,
        query: str,
        limit: int = 5,
    ) -> list[MemoryMessage]:
        """语义检索（利用 mem0 的向量搜索能力）。"""
        client = await self._get_client()
        try:
            result = await client.search(query, run_id=session_id, limit=limit)
        except Exception:
            return []

        memories = result.get("results", []) if isinstance(result, dict) else []
        return [_mem0_to_msg(m, session_id) for m in memories]

    async def cross_session_search(
        self,
        scope: MemoryScope,
        query: str,
        limit: int = 10,
    ) -> list[MemoryMessage]:
        """跨 session 语义检索。

        利用 mem0 的 user_id / agent_id 维度进行跨 session 搜索。
        """
        scope.validate()
        client = await self._get_client()

        search_kwargs: dict[str, Any] = {"limit": limit}

        if scope.session_id:
            search_kwargs["run_id"] = scope.session_id
        if scope.user_id:
            search_kwargs["user_id"] = scope.user_id
        if scope.channel:
            search_kwargs["agent_id"] = scope.channel
        # global_access: 不加 scope 限制

        try:
            result = await client.search(query, **search_kwargs)
        except Exception:
            return []

        memories = result.get("results", []) if isinstance(result, dict) else []
        return [_mem0_to_msg(m, scope.session_id or "") for m in memories]

    async def count(self, session_id: str) -> int:
        """统计指定 session 的记忆条数。"""
        client = await self._get_client()
        try:
            result = await client.get_all(run_id=session_id)
            memories = result.get("results", []) if isinstance(result, dict) else []
            return len(memories)
        except Exception:
            return 0


# ── 内部工具 ──────────────────────────────────────────────

def _mem0_to_msg(mem: dict[str, Any], default_session_id: str) -> MemoryMessage:
    """将 mem0 返回的记忆条目转换为 MemoryMessage。"""
    return MemoryMessage(
        id=mem.get("id"),
        session_id=mem.get("run_id", default_session_id),
        role=MemoryRole.ASSISTANT,   # mem0 提取的是事实，默认标记为 assistant
        content=mem.get("memory", mem.get("content", "")),
        created_at=_parse_datetime(mem.get("created_at")),
        metadata=mem.get("metadata", {}),
        user_id=mem.get("user_id"),
        channel=mem.get("agent_id"),
        memory_type="personal",  # mem0 主要存储个人事实记忆
    )


def _parse_datetime(dt_str: Any) -> datetime:
    """安全解析时间字符串，失败时返回当前 UTC 时间。"""
    if isinstance(dt_str, datetime):
        return dt_str
    if isinstance(dt_str, str):
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass
    return datetime.now(timezone.utc)
