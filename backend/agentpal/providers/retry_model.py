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
import logging
import os
from typing import Any, AsyncGenerator

from agentscope.model import ChatModelBase

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

    def __init__(self, inner: ChatModelBase) -> None:
        super().__init__(model_name=inner.model_name, stream=inner.stream)
        self._inner = inner

    @property
    def inner_class(self) -> type:
        """暴露底层模型类型，供上层做 formatter 映射时使用。"""
        return self._inner.__class__

    async def __call__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """代理调用，失败时自动重试。"""
        max_attempts = LLM_MAX_RETRIES + 1
        last_exc: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                result = await self._inner(*args, **kwargs)

                if isinstance(result, AsyncGenerator):
                    # 流式：用包装生成器覆盖，在中途失败时重试
                    return self._wrap_stream(result, args, kwargs, attempt, max_attempts)
                return result

            except Exception as exc:
                last_exc = exc
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
        try:
            async for chunk in stream:
                yield chunk
        except Exception as exc:
            failed_exc = exc
        finally:
            await stream.aclose()

        if failed_exc is None:
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
                await asyncio.sleep(retry_delay)
