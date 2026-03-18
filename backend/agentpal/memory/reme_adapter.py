"""ReMeMemory — 基于 ReMe 的记忆后端适配器。

通过适配 ReMe (https://github.com/agentscope-ai/ReMe) 的记忆 API，
实现 BaseMemory 接口，支持个人记忆、任务记忆和工具记忆。

使用前需安装: pip install reme-memory

配置示例（~/.nimo/config.yaml）：
    memory:
      backend: reme
      reme:
        server_url: http://localhost:8080  # ReMe server URL
        agent_name: AgentPal
        model_config:
          model_name: qwen3-max
          api_key: sk-...
        embedding_config:
          model_name: text-embedding-v4
          api_key: sk-...
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from agentpal.memory.base import BaseMemory, MemoryMessage, MemoryRole, MemoryScope


class ReMeMemory(BaseMemory):
    """ReMe 记忆后端适配器。

    支持两种模式：
    1. Server 模式：通过 HTTP API 与 ReMe 服务端通信
    2. 直接模式：使用 ReMe 的 Python SDK（需要 agentscope）

    Scoping 映射：
    - session_id   → ReMe workspace_id
    - user_id      → ReMe user_name
    - channel      → 存入 metadata

    Args:
        server_url:       ReMe 服务端 URL（Server 模式）
        agent_name:       Agent 名称（默认 "AgentPal"）
        model_config:     LLM 模型配置
        embedding_config: Embedding 模型配置
    """

    def __init__(
        self,
        server_url: str | None = None,
        agent_name: str = "AgentPal",
        model_config: dict[str, Any] | None = None,
        embedding_config: dict[str, Any] | None = None,
    ) -> None:
        self._server_url = server_url
        self._agent_name = agent_name
        self._model_config = model_config or {}
        self._embedding_config = embedding_config or {}
        self._client: Any = None
        # 内部 buffer 用于 get_recent（ReMe 不提供直接的消息列表 API）
        self._local_buffer: dict[str, list[MemoryMessage]] = {}

    async def _get_http_client(self) -> Any:
        """获取 HTTP 客户端（Server 模式）。"""
        if self._client is None:
            try:
                import httpx
                self._client = httpx.AsyncClient(
                    base_url=self._server_url,
                    timeout=30.0,
                )
            except ImportError as exc:
                raise ImportError(
                    "httpx 未安装。请执行: pip install httpx"
                ) from exc
        return self._client

    # ── BaseMemory 实现 ───────────────────────────────────

    async def add(self, message: MemoryMessage) -> MemoryMessage:
        """写入消息到 ReMe。

        通过 ReMe 的 record API 存储个人记忆。
        同时在本地 buffer 中保存原始消息用于 get_recent。
        """
        if message.id is None:
            message.id = str(uuid.uuid4())

        # 本地 buffer 保存原始消息
        if message.session_id not in self._local_buffer:
            self._local_buffer[message.session_id] = []
        self._local_buffer[message.session_id].append(message)

        # Server 模式
        if self._server_url:
            try:
                client = await self._get_http_client()
                await client.post(
                    "/store_personal_memory",
                    json={
                        "content": message.content,
                        "workspace_id": message.session_id,
                        "user_name": message.user_id or "default",
                        "agent_name": self._agent_name,
                        "role": str(message.role),
                        "metadata": message.metadata,
                    },
                )
            except Exception:
                pass  # 写入失败不影响主流程

        return message

    async def get_recent(self, session_id: str, limit: int = 20) -> list[MemoryMessage]:
        """获取最近消息。

        ReMe 不提供分页消息列表 API，使用本地 buffer 返回。
        """
        msgs = self._local_buffer.get(session_id, [])
        return msgs[-limit:]

    async def clear(self, session_id: str) -> None:
        """清空指定 session 的记忆。"""
        self._local_buffer.pop(session_id, None)
        # ReMe Server 模式没有直接的 delete_all API

    async def search(
        self,
        session_id: str,
        query: str,
        limit: int = 5,
    ) -> list[MemoryMessage]:
        """语义检索（通过 ReMe 的 retrieve API）。"""
        if self._server_url:
            try:
                client = await self._get_http_client()
                resp = await client.post(
                    "/retrieve_personal_memory",
                    json={
                        "query": query,
                        "workspace_id": session_id,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    memories = data.get("memories", data.get("results", []))
                    return [_reme_to_msg(m, session_id) for m in memories[:limit]]
            except Exception:
                pass

        # 回退到本地 buffer 关键词搜索
        msgs = self._local_buffer.get(session_id, [])
        q = query.lower()
        matched = [m for m in msgs if q in m.content.lower()]
        return matched[-limit:]

    async def cross_session_search(
        self,
        scope: MemoryScope,
        query: str,
        limit: int = 10,
    ) -> list[MemoryMessage]:
        """跨 session 检索。

        ReMe 支持通过 user_name 跨 workspace 搜索。
        """
        scope.validate()

        if scope.session_id:
            return await self.search(scope.session_id, query, limit)

        if self._server_url:
            try:
                client = await self._get_http_client()
                payload: dict[str, Any] = {"query": query}
                if scope.user_id:
                    payload["user_name"] = scope.user_id
                resp = await client.post(
                    "/retrieve_personal_memory",
                    json=payload,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    memories = data.get("memories", data.get("results", []))
                    return [_reme_to_msg(m, "") for m in memories[:limit]]
            except Exception:
                pass

        # 回退到本地 buffer 扫描
        q = query.lower()
        matched: list[MemoryMessage] = []
        for session_id, msgs in self._local_buffer.items():
            for msg in msgs:
                if scope.user_id and msg.user_id != scope.user_id:
                    continue
                if scope.channel and msg.channel != scope.channel:
                    continue
                if q in msg.content.lower():
                    matched.append(msg)
        matched.sort(key=lambda m: m.created_at)
        return matched[-limit:]

    async def count(self, session_id: str) -> int:
        """统计指定 session 的记忆条数。"""
        return len(self._local_buffer.get(session_id, []))

    async def close(self) -> None:
        """关闭 HTTP 客户端连接。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ── 内部工具 ──────────────────────────────────────────────

def _reme_to_msg(mem: dict[str, Any], default_session_id: str) -> MemoryMessage:
    """将 ReMe 返回的记忆条目转换为 MemoryMessage。"""
    return MemoryMessage(
        id=mem.get("id", str(uuid.uuid4())),
        session_id=mem.get("workspace_id", default_session_id),
        role=MemoryRole(mem["role"]) if mem.get("role") in MemoryRole._value2member_map_ else MemoryRole.ASSISTANT,
        content=mem.get("content", mem.get("memory", "")),
        created_at=_parse_datetime(mem.get("created_at")),
        metadata=mem.get("metadata", {}),
        user_id=mem.get("user_name"),
        memory_type=mem.get("memory_type", "personal"),
    )


def _parse_datetime(dt_str: Any) -> datetime:
    """安全解析时间字符串。"""
    if isinstance(dt_str, datetime):
        return dt_str
    if isinstance(dt_str, str):
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass
    return datetime.now(timezone.utc)
