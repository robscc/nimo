"""RetryChatModel — 对任意 ChatModelBase 的透明重试包装。

对瞬时错误（限流、超时、连接中断）使用指数退避自动重试，
流式响应在中途失败时也会从头重试。

环境变量（可选）：
    AGENTPAL_LLM_MAX_RETRIES   重试次数上限（默认 3）
    AGENTPAL_LLM_BACKOFF_BASE  初始等待秒数（默认 1.0）
    AGENTPAL_LLM_BACKOFF_CAP   最大等待上限秒数（默认 10.0）
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from time import time
from typing import Any, AsyncGenerator

from agentscope.model import ChatModelBase

# 重试回调签名：(attempt, max_attempts, error_message, delay_seconds) -> None
RetryCallback = Callable[[int, int, str, float], Any]

logger = logging.getLogger(__name__)

# ── 配置常量（可通过环境变量覆盖）────────────────────────────────────────
LLM_MAX_RETRIES = int(os.getenv("AGENTPAL_LLM_MAX_RETRIES", "3"))
LLM_BACKOFF_BASE = float(os.getenv("AGENTPAL_LLM_BACKOFF_BASE", "1.0"))
LLM_BACKOFF_CAP = float(os.getenv("AGENTPAL_LLM_BACKOFF_CAP", "10.0"))

# HTTP 状态码可重试范围
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# 懒加载可重试异常类型
_openai_retryable: tuple[type[Exception], ...] | None = None


def _get_retryable_exceptions() -> tuple[type[Exception], ...]:
    global _openai_retryable  # noqa: PLW0603
    if _openai_retryable is None:
        exceptions: list[type[Exception]] = []
        try:
            import openai  # noqa: PLC0415

            exceptions.extend(
                [
                    openai.RateLimitError,
                    openai.APITimeoutError,
                    openai.APIConnectionError,
                ]
            )
        except ImportError:
            pass
        _openai_retryable = tuple(exceptions)
    return _openai_retryable


def _is_retryable(exc: Exception) -> bool:
    """判断异常是否属于可重试的瞬时错误。"""
    retryable = _get_retryable_exceptions()
    if retryable and isinstance(exc, retryable):
        return True
    status = getattr(exc, "status_code", None)
    if status is not None and status in RETRYABLE_STATUS_CODES:
        return True
    return False


def _backoff(attempt: int) -> float:
    """指数退避：base * 2^(attempt-1)，上限 cap。"""
    return min(LLM_BACKOFF_CAP, LLM_BACKOFF_BASE * (2 ** max(0, attempt - 1)))


class RetryChatModel(ChatModelBase):
    """透明重试包装器。

    用法::

        inner = OpenAIChatModel(...)
        model = RetryChatModel(inner)
        # 之后完全按照 inner 的方式使用
        response = await model(messages)
        # 或流式
        async for chunk in await model(messages, stream=True):
            ...
    """

    def __init__(
        self,
        inner: ChatModelBase,
        on_retry: RetryCallback | None = None,
    ) -> None:
        super().__init__(model_name=inner.model_name, stream=inner.stream)
        self._inner = inner
        self._on_retry = on_retry

    @property
    def inner_class(self) -> type:
        """暴露底层模型类型，供上层做 formatter 映射时使用。"""
        return self._inner.__class__

    def _notify_retry(
        self, attempt: int, max_attempts: int, exc: Exception, delay: float,
    ) -> None:
        """通知重试回调（如果已注册）。"""
        if self._on_retry is not None:
            self._on_retry(attempt, max_attempts, str(exc), delay)

    @staticmethod
    def _fmt_request(args: tuple, kwargs: dict) -> str:
        """格式化请求参数用于日志输出。"""
        parts: list[str] = []
        # args[0] 通常是 messages
        if args:
            messages = args[0]
            if isinstance(messages, list):
                parts.append(f"messages({len(messages)}): [")
                for m in messages:
                    role = m.get("role", "?") if isinstance(m, dict) else "?"
                    content = m.get("content", "") if isinstance(m, dict) else str(m)
                    # 截断过长内容
                    if isinstance(content, str) and len(content) > 200:
                        content = content[:200] + "…"
                    tc = m.get("tool_calls") if isinstance(m, dict) else None
                    line = f"  {{{role}: {content!r}"
                    if tc:
                        line += f", tool_calls: {len(tc)}"
                    line += "}"
                    parts.append(line)
                parts.append("]")
        # kwargs 里的 tools
        tools = kwargs.get("tools")
        if tools and isinstance(tools, list):
            names = [t.get("function", {}).get("name", "?") if isinstance(t, dict) else "?" for t in tools]
            parts.append(f"tools: {names}")
        # 其他 kwargs（排除 messages/tools）
        extra = {k: v for k, v in kwargs.items() if k not in ("tools",)}
        if extra:
            parts.append(f"kwargs: {extra}")
        return "\n".join(parts)

    @staticmethod
    def _fmt_response(result: Any) -> str:
        """格式化响应用于日志输出。"""
        if isinstance(result, AsyncGenerator):
            return "<AsyncGenerator (streaming)>"
        # agentscope ChatResponse
        content = getattr(result, "content", None)
        usage = getattr(result, "usage", None)
        parts: list[str] = []
        if content:
            try:
                summary = json.dumps(content, ensure_ascii=False, default=str)
                if len(summary) > 500:
                    summary = summary[:500] + "…"
                parts.append(f"content: {summary}")
            except Exception:
                parts.append(f"content: {content!r}"[:500])
        if usage:
            parts.append(f"usage: {usage}")
        return " | ".join(parts) if parts else repr(result)[:300]

    async def __call__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """代理调用，失败时自动重试。"""
        max_attempts = LLM_MAX_RETRIES + 1
        last_exc: Exception | None = None

        # ── 请求日志 ──
        base_url = ""
        client = getattr(self._inner, "client", None)
        if client:
            base_url = str(getattr(client, "base_url", ""))
        logger.info(
            "LLM 请求 [model=%s, stream=%s, base_url=%s]\n%s",
            self._inner.model_name,
            self._inner.stream,
            base_url or "?",
            self._fmt_request(args, kwargs),
        )

        for attempt in range(1, max_attempts + 1):
            t0 = time()
            try:
                result = await self._inner(*args, **kwargs)

                if isinstance(result, AsyncGenerator):
                    # 流式：用包装生成器覆盖，在中途失败时重试
                    logger.info(
                        "LLM 响应 [model=%s, attempt=%d/%d, %.1fs] → streaming started",
                        self._inner.model_name, attempt, max_attempts, time() - t0,
                    )
                    return self._wrap_stream(result, args, kwargs, attempt, max_attempts)

                # ── 非流式响应日志 ──
                logger.info(
                    "LLM 响应 [model=%s, attempt=%d/%d, %.1fs]\n%s",
                    self._inner.model_name, attempt, max_attempts, time() - t0,
                    self._fmt_response(result),
                )
                return result

            except Exception as exc:
                last_exc = exc
                elapsed = time() - t0
                # 构建异常链信息
                cause_parts: list[str] = []
                cause = exc.__cause__
                while cause:
                    cause_parts.append(f"{type(cause).__name__}: {cause or '(no detail)'}")
                    cause = cause.__cause__
                cause_info = " → ".join(cause_parts) if cause_parts else ""
                logger.error(
                    "LLM 错误 [model=%s, attempt=%d/%d, %.1fs] %s: %s%s",
                    self._inner.model_name, attempt, max_attempts, elapsed,
                    type(exc).__name__, exc,
                    f" | cause: {cause_info}" if cause_info else "",
                )
                if not _is_retryable(exc) or attempt >= max_attempts:
                    raise
                delay = _backoff(attempt)
                logger.warning(
                    "LLM 调用失败（第 %d/%d 次）：%s，%.1f 秒后重试…",
                    attempt,
                    max_attempts,
                    exc,
                    delay,
                )
                self._notify_retry(attempt, max_attempts, exc, delay)
                await asyncio.sleep(delay)

        raise last_exc  # type: ignore[misc]

    async def _wrap_stream(
        self,
        stream: AsyncGenerator,
        call_args: tuple,
        call_kwargs: dict,
        current_attempt: int,
        max_attempts: int,
    ) -> AsyncGenerator:
        """消费流，在中途失败时从头重试整个请求。"""
        failed_exc: Exception | None = None
        t0 = time()
        last_chunk: Any = None
        chunk_count = 0
        try:
            async for chunk in stream:
                last_chunk = chunk
                chunk_count += 1
                yield chunk
        except Exception as exc:
            failed_exc = exc
        finally:
            await stream.aclose()

        if failed_exc is None:
            # 流式完成，打印最终响应摘要
            logger.info(
                "LLM 流式完成 [model=%s, chunks=%d, %.1fs]\n%s",
                self._inner.model_name, chunk_count, time() - t0,
                self._fmt_response(last_chunk) if last_chunk else "(empty)",
            )
            return

        if not _is_retryable(failed_exc) or current_attempt >= max_attempts:
            raise failed_exc

        delay = _backoff(current_attempt)
        logger.warning(
            "LLM 流式中断（第 %d/%d 次）：%s，%.1f 秒后重试…",
            current_attempt,
            max_attempts,
            failed_exc,
            delay,
        )
        self._notify_retry(current_attempt, max_attempts, failed_exc, delay)
        await asyncio.sleep(delay)

        for attempt in range(current_attempt + 1, max_attempts + 1):
            new_stream: AsyncGenerator | None = None
            try:
                result = await self._inner(*call_args, **call_kwargs)
                if isinstance(result, AsyncGenerator):
                    new_stream = result
                    async for chunk in new_stream:
                        yield chunk
                    new_stream = None
                else:
                    yield result
                return
            except Exception as retry_exc:
                if new_stream is not None:
                    await new_stream.aclose()
                    new_stream = None
                if not _is_retryable(retry_exc) or attempt >= max_attempts:
                    raise
                retry_delay = _backoff(attempt)
                logger.warning(
                    "LLM 流式重试失败（第 %d/%d 次）：%s，%.1f 秒后重试…",
                    attempt,
                    max_attempts,
                    retry_exc,
                    retry_delay,
                )
                self._notify_retry(attempt, max_attempts, retry_exc, retry_delay)
                await asyncio.sleep(retry_delay)
