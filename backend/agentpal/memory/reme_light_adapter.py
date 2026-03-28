"""ReMeLightMemory — 基于 ReMeLight 的本地文件记忆后端适配器。

ReMeLight 是 ReMe 的本地文件模式，无需启动独立服务，直接在进程内运行，
支持本地文件持久化 + 混合向量+BM25 检索 + 工作记忆管理。

核心理念：直接采用 ReMeLight 原生记忆管理方案，而非自己维护 buffer。

使用前需安装: pip install reme-ai
Import: from reme.reme_light import ReMeLight

配置示例（~/.nimo/config.yaml）：
    memory:
      backend: reme_light
      reme_light:
        working_dir: .reme
        llm_api_key: sk-...
        llm_base_url: https://...
        embedding_api_key: sk-...
        embedding_base_url: https://...
        vector_weight: 0.7
        candidate_multiplier: 3.0
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from agentscope.message import Msg

from agentpal.memory.base import BaseMemory, MemoryMessage, MemoryRole, MemoryScope

logger = logging.getLogger(__name__)

# ── Role 映射 ─────────────────────────────────────────────

_ROLE_MAP = {"user": "user", "assistant": "assistant", "system": "system", "tool": "user"}


def _memory_role_to_str(role) -> str:
    """将 MemoryRole 映射为 ReMeLight 接受的 role 字符串。"""
    return _ROLE_MAP.get(str(role), "user")


# ── Session tag 辅助函数 ──────────────────────────────────

_SESSION_TAG_RE = re.compile(r"\[session:([^\]]+)\]\s*")


def _tag_content(session_id: str, content: str) -> str:
    """给内容添加 session tag 前缀，用于向量索引中的 session 过滤。"""
    return f"[session:{session_id}] {content}"


def _strip_session_tag(content: str) -> str:
    """移除内容中的 session tag 前缀。"""
    return _SESSION_TAG_RE.sub("", content, count=1)


def _extract_session_id(content: str) -> str | None:
    """从 tagged content 中提取 session_id。"""
    m = _SESSION_TAG_RE.match(content)
    return m.group(1) if m else None


def _extract_items_from_result(result: Any) -> list[dict[str, Any]]:
    """适配 ToolResponse 多种可能结构，提取结果列表。

    ReMeLight 的 memory_search 可能返回不同格式：
    - list[dict]  → 直接返回
    - ToolResponse with .items  → 取 .items
    - ToolResponse with .content (str)  → 返回单条
    - 其他 → 空列表
    """
    if isinstance(result, list):
        return result
    if hasattr(result, "items"):
        items = result.items
        if isinstance(items, list):
            return items
    if hasattr(result, "content"):
        content = result.content
        if isinstance(content, list):
            return content
        if isinstance(content, str) and content.strip():
            return [{"content": content}]
    return []


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


class ReMeLightMemory(BaseMemory):
    """ReMeLight 本地文件记忆后端适配器。

    直接采用 ReMeLight 原生记忆管理方案：
    - 单一全局 ReMeLight 实例（嵌入模型只加载一次）
    - 消息存储：使用 ReMeInMemoryMemory 做会话消息管理，自动持久化到 jsonl
    - 搜索检索：使用 memory_search() 做混合向量+BM25 语义检索
    - 工作记忆：暴露 compact_memory() / summary_memory() / pre_reasoning_hook() 等原生能力
    - 延迟初始化：首次使用时 import + await start()，配 asyncio.Lock 防并发
    - _session_messages 辅助 buffer 仅用于 BaseMemory 接口兼容

    Args:
        working_dir:           ReMeLight 工作目录（默认 ".reme"）
        llm_api_key:           LLM API Key
        llm_base_url:          LLM API Base URL
        embedding_api_key:     Embedding API Key
        embedding_base_url:    Embedding API Base URL
        llm_model_config:      LLM 模型配置字典
        embedding_model_config: Embedding 模型配置字典
        vector_weight:         向量检索权重（0~1，默认 0.7）
        candidate_multiplier:  候选倍数（默认 3.0）
    """

    def __init__(
        self,
        working_dir: str = ".reme",
        llm_api_key: str | None = None,
        llm_base_url: str | None = None,
        embedding_api_key: str | None = None,
        embedding_base_url: str | None = None,
        llm_model_config: dict[str, Any] | None = None,
        embedding_model_config: dict[str, Any] | None = None,
        vector_weight: float = 0.7,
        candidate_multiplier: float = 3.0,
    ) -> None:
        self._working_dir = working_dir
        self._llm_api_key = llm_api_key
        self._llm_base_url = llm_base_url
        self._embedding_api_key = embedding_api_key
        self._embedding_base_url = embedding_base_url
        self._llm_model_config = llm_model_config or {}
        self._embedding_model_config = embedding_model_config or {}
        self._vector_weight = vector_weight
        self._candidate_multiplier = candidate_multiplier

        self._reme: Any = None
        self._in_memory: Any = None  # ReMeInMemoryMemory
        self._init_lock = asyncio.Lock()
        self._started = False

        # 辅助 buffer：仅用于 BaseMemory 接口兼容（get_recent 返回 MemoryMessage 列表）
        self._session_messages: dict[str, list[MemoryMessage]] = {}

    async def _ensure_started(self) -> None:
        """延迟初始化 ReMeLight 实例。

        首次调用时 import + 实例化 + await start()，
        使用 asyncio.Lock 防止并发初始化。
        """
        if self._started:
            return

        async with self._init_lock:
            if self._started:
                return

            try:
                from reme.reme_light import ReMeLight
            except ImportError as exc:
                raise ImportError(
                    "reme-ai 未安装。请执行: pip install reme-ai"
                ) from exc

            kwargs: dict[str, Any] = {
                "working_dir": self._working_dir,
            }

            # LLM 配置
            if self._llm_api_key:
                kwargs["llm_api_key"] = self._llm_api_key
            if self._llm_base_url:
                kwargs["llm_base_url"] = self._llm_base_url
            if self._llm_model_config:
                kwargs["llm_model_config"] = self._llm_model_config

            # Embedding 配置
            if self._embedding_api_key:
                kwargs["embedding_api_key"] = self._embedding_api_key
            if self._embedding_base_url:
                kwargs["embedding_base_url"] = self._embedding_base_url
            if self._embedding_model_config:
                kwargs["embedding_model_config"] = self._embedding_model_config

            self._reme = ReMeLight(**kwargs)
            await self._reme.start()

            # 获取 ReMeInMemoryMemory 实例
            self._in_memory = self._reme.get_in_memory_memory()
            self._started = True

    # ── BaseMemory 必选实现 ────────────────────────────────

    async def add(self, message: MemoryMessage) -> MemoryMessage:
        """写入消息到 ReMeLight。

        1. 存入 _session_messages 做 MemoryMessage 格式兼容
        2. 调用 ReMeLight ReMeInMemoryMemory 存储（原生持久化到 jsonl）

        空内容消息只存 buffer，不发往 ReMeLight。
        """
        if message.id is None:
            message.id = str(uuid.uuid4())

        # 辅助 buffer 保存
        if message.session_id not in self._session_messages:
            self._session_messages[message.session_id] = []
        self._session_messages[message.session_id].append(message)

        # 空内容跳过原生存储
        if not message.content or not message.content.strip():
            return message

        # ReMeLight 原生存储
        try:
            await self._ensure_started()
            tagged = _tag_content(message.session_id, message.content)
            msg = Msg(
                name=_memory_role_to_str(message.role),
                content=tagged,
                role=_memory_role_to_str(message.role),
            )
            await self._in_memory.add(memories=msg)
        except Exception:
            logger.warning("ReMeLight add 失败，消息已保存至本地 buffer", exc_info=True)

        return message

    async def get_recent(self, session_id: str, limit: int = 20) -> list[MemoryMessage]:
        """获取最近消息。

        优先从 ReMeLight 持久化存储读取，按 session_id 过滤。
        如果 ReMeLight 不可用或失败，回退到内存 buffer。
        """
        # 先尝试从 ReMeLight 读取
        try:
            await self._ensure_started()
            memories = await self._in_memory.get_memory()
            if memories:
                matched: list[MemoryMessage] = []
                # 倒序遍历，取最近的
                for mem in reversed(memories):
                    content = getattr(mem, "content", "") or ""
                    extracted_sid = _extract_session_id(content)
                    if extracted_sid == session_id:
                        role = getattr(mem, "role", "assistant") or "assistant"
                        name = getattr(mem, "name", role)
                        matched.append(
                            MemoryMessage(
                                id=getattr(mem, "id", None) or str(uuid.uuid4()),
                                session_id=session_id,
                                role=_safe_role(name) if name else MemoryRole.ASSISTANT,
                                content=_strip_session_tag(content),
                                created_at=_parse_datetime(getattr(mem, "created_at", None)),
                                metadata={},
                            )
                        )
                    if len(matched) >= limit:
                        break
                # 按时间正序返回（最新的在末尾）
                return list(reversed(matched))
        except Exception:
            logger.warning("ReMeLight get_memory 失败，回退到内存 buffer", exc_info=True)

        # 回退到内存 buffer
        msgs = self._session_messages.get(session_id, [])
        return msgs[-limit:]

    async def clear(self, session_id: str) -> None:
        """清空指定 session 的记忆。

        先调用 ReMeLight clear_content() 触发持久化（同步方法），再清 buffer。
        """
        self._session_messages.pop(session_id, None)
        if self._in_memory is not None:
            try:
                self._in_memory.clear_content()
            except Exception:
                logger.warning("ReMeLight clear_content 失败", exc_info=True)

    # ── BaseMemory 可选覆盖 ────────────────────────────────

    async def search(
        self,
        session_id: str,
        query: str,
        limit: int = 5,
    ) -> list[MemoryMessage]:
        """语义检索（通过 ReMeLight 的 memory_search）。

        使用混合向量+BM25 检索，按 session_id 后过滤。
        失败时回退到 buffer 关键词搜索。
        """
        try:
            await self._ensure_started()
            tagged_query = _tag_content(session_id, query)
            # 请求更多候选，后过滤 session
            fetch_limit = int(limit * self._candidate_multiplier)
            result = await self._reme.memory_search(
                query=tagged_query,
                max_results=fetch_limit,
            )
            items = _extract_items_from_result(result)

            # Session 过滤
            matched: list[MemoryMessage] = []
            for item in items:
                content = item.get("content", "")
                extracted_sid = _extract_session_id(content)
                if extracted_sid == session_id:
                    matched.append(
                        MemoryMessage(
                            id=item.get("id", str(uuid.uuid4())),
                            session_id=session_id,
                            role=_safe_role(item.get("role")),
                            content=_strip_session_tag(content),
                            created_at=_parse_datetime(item.get("created_at")),
                            metadata=item.get("metadata", {}),
                        )
                    )
                if len(matched) >= limit:
                    break

            return matched
        except Exception:
            logger.warning("ReMeLight search 失败，回退到 buffer 关键词搜索", exc_info=True)

        # 回退到 buffer 关键词搜索
        msgs = self._session_messages.get(session_id, [])
        q = query.lower()
        matched_fallback = [m for m in msgs if q in m.content.lower()]
        return matched_fallback[-limit:]

    async def cross_session_search(
        self,
        scope: MemoryScope,
        query: str,
        limit: int = 10,
    ) -> list[MemoryMessage]:
        """跨 session 检索（全局 memory_search）。

        失败时回退到 buffer 扫描。
        """
        scope.validate()

        if scope.session_id:
            return await self.search(scope.session_id, query, limit)

        try:
            await self._ensure_started()
            fetch_limit = int(limit * self._candidate_multiplier)
            result = await self._reme.memory_search(
                query=query,
                max_results=fetch_limit,
            )
            items = _extract_items_from_result(result)

            matched: list[MemoryMessage] = []
            for item in items:
                content = item.get("content", "")
                sid = _extract_session_id(content) or ""
                matched.append(
                    MemoryMessage(
                        id=item.get("id", str(uuid.uuid4())),
                        session_id=sid,
                        role=_safe_role(item.get("role")),
                        content=_strip_session_tag(content),
                        created_at=_parse_datetime(item.get("created_at")),
                        metadata=item.get("metadata", {}),
                    )
                )
                if len(matched) >= limit:
                    break

            return matched
        except Exception:
            logger.warning("ReMeLight cross_session_search 失败，回退到 buffer", exc_info=True)

        # 回退到 buffer 扫描
        q = query.lower()
        all_matched: list[MemoryMessage] = []
        for sid, msgs in self._session_messages.items():
            for msg in msgs:
                if scope.user_id and msg.user_id != scope.user_id:
                    continue
                if scope.channel and msg.channel != scope.channel:
                    continue
                if q in msg.content.lower():
                    all_matched.append(msg)
        all_matched.sort(key=lambda m: m.created_at)
        return all_matched[-limit:]

    async def count(self, session_id: str) -> int:
        """统计指定 session 的记忆条数。"""
        return len(self._session_messages.get(session_id, []))

    async def close(self) -> None:
        """关闭 ReMeLight 实例。"""
        if self._reme is not None:
            try:
                await self._reme.close()
            except Exception:
                logger.warning("ReMeLight close 失败", exc_info=True)
            self._reme = None
            self._in_memory = None
            self._started = False

    # ── ReMeLight 原生能力（直接暴露，供 Agent 层调用）────────

    async def compact_history(self, session_id: str) -> str | None:
        """调用 compact_memory() 压缩长对话历史。

        Returns:
            压缩后的摘要文本，失败返回 None
        """
        try:
            await self._ensure_started()
            messages = self._in_memory.get_memory()
            if not messages:
                return None
            result = await self._reme.compact_memory(messages=messages)
            return str(result) if result else None
        except Exception:
            logger.warning("ReMeLight compact_history 失败", exc_info=True)
            return None

    async def summarize_session(self, session_id: str) -> str | None:
        """调用 summary_memory() 持久化摘要到文件。

        Returns:
            摘要文本，失败返回 None
        """
        try:
            await self._ensure_started()
            messages = self._in_memory.get_memory()
            if not messages:
                return None
            result = await self._reme.summary_memory(messages=messages)
            return str(result) if result else None
        except Exception:
            logger.warning("ReMeLight summarize_session 失败", exc_info=True)
            return None

    async def pre_reasoning(
        self,
        session_id: str,
        system_prompt: str | None = None,
        compressed_summary: str | None = None,
    ) -> dict[str, Any] | None:
        """调用 pre_reasoning_hook() 完整管线。

        包含：工具结果压缩 + 上下文检查 + 历史压缩 + 摘要。

        Args:
            session_id:          会话 ID
            system_prompt:       系统提示词
            compressed_summary:  已有的压缩摘要

        Returns:
            pre_reasoning 结果字典，失败返回 None
        """
        try:
            await self._ensure_started()
            messages = self._in_memory.get_memory()
            if not messages:
                return None
            result = await self._reme.pre_reasoning_hook(
                messages=messages,
                system_prompt=system_prompt or "",
                compressed_summary=compressed_summary or "",
            )
            # pre_reasoning_hook 可能返回 tuple[list[Msg], str]
            if isinstance(result, tuple):
                kept_messages, summary = result
                return {"messages_count": len(kept_messages), "compressed_summary": summary}
            if isinstance(result, dict):
                return result
            return {"result": str(result)} if result else None
        except Exception:
            logger.warning("ReMeLight pre_reasoning 失败", exc_info=True)
            return None

    def get_reme_instance(self) -> Any:
        """返回底层 ReMeLight 实例，允许直接调用任何原生 API。

        注意：调用前需确保已 await _ensure_started()。

        Returns:
            ReMeLight 实例，未初始化时返回 None
        """
        return self._reme


# ── 内部工具 ──────────────────────────────────────────────


def _safe_role(role: Any) -> MemoryRole | str:
    """安全转换 role 值。"""
    if role and str(role) in MemoryRole._value2member_map_:
        return MemoryRole(str(role))
    return MemoryRole.ASSISTANT
