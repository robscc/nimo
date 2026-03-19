"""HTTPAgentRuntime — 远程 HTTP Agent 运行时适配器。

支持连接远程 Agent 服务，如：
- pi-mono: 统一的 Agent API 网关
- OpenClaw: 开源 Agent 框架
- 其他兼容的 HTTP Agent 服务

协议规范：
- POST /chat: 非流式对话
- POST /chat/stream: 流式对话（SSE）
- POST /cancel: 取消任务
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator

import aiohttp
from loguru import logger

from agentpal.runtimes.base import (
    BaseAgentRuntime,
    ExecutionResult,
    RuntimeConfig,
    RuntimeStatus,
)


class HTTPAgentRuntime(BaseAgentRuntime):
    """远程 HTTP Agent 运行时适配器。

    通过 HTTP 协议与远程 Agent 服务通信，
    支持流式和非流式两种模式。
    """

    def __init__(
        self,
        session_id: str,
        config: RuntimeConfig,
        db: Any | None = None,
        memory: Any | None = None,
        parent_session_id: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        """初始化运行时。

        Args:
            session_id: 会话 ID
            config: 运行时配置
            db: 数据库 session（可选）
            memory: 记忆模块（可选）
            parent_session_id: 父会话 ID
            base_url: 远程服务基础 URL
            api_key: API 密钥（可选）
        """
        super().__init__(
            session_id=session_id,
            config=config,
            db=db,
            memory=memory,
            parent_session_id=parent_session_id,
        )

        # 从配置或参数中获取 URL
        self.base_url = base_url or config.extra.get("base_url", "http://localhost:8000")
        self.api_key = api_key or config.extra.get("api_key")

        # HTTP 会话
        self._session: aiohttp.ClientSession | None = None
        self._cancel_flag = False
        self._current_request_ctx: aiohttp.ClientResponse | None = None

    async def _initialize(self) -> None:
        """初始化 HTTP 会话。"""
        connector = aiohttp.TCPConnector(limit=10, ttl_dns_cache=300)
        self._session = aiohttp.ClientSession(connector=connector)

        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        self._session._default_headers.update(headers)

        logger.info(f"HTTPAgentRuntime initialized for {self.base_url}")

    async def _execute_core(self, task_prompt: str, **kwargs: Any) -> ExecutionResult:
        """执行任务（非流式）。

        Args:
            task_prompt: 任务提示词
            **kwargs: 额外参数

        Returns:
            ExecutionResult: 执行结果
        """
        if self._session is None:
            raise RuntimeError("HTTP session not initialized")

        start_time = asyncio.get_event_loop().time()

        # 构建请求体
        payload = {
            "session_id": self.session_id,
            "messages": [
                {"role": "user", "content": task_prompt}
            ],
            "model": self.config.model_config.get("model") if self.config.model_config else None,
            "max_tokens": kwargs.get("max_tokens"),
            "temperature": kwargs.get("temperature", 0.7),
        }

        # 添加工具定义（如果有）
        if "tools" in kwargs:
            payload["tools"] = kwargs["tools"]

        try:
            async with self._session.post(
                f"{self.base_url}/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds),
            ) as resp:
                self._current_request_ctx = resp

                if resp.status != 200:
                    error_text = await resp.text()
                    return ExecutionResult(
                        success=False,
                        error=f"HTTP {resp.status}: {error_text}",
                        metadata={"status_code": resp.status},
                    )

                result_data = await resp.json()

                elapsed = asyncio.get_event_loop().time() - start_time

                # 提取回复文本
                output_text = ""
                if "choices" in result_data and result_data["choices"]:
                    output_text = result_data["choices"][0].get("message", {}).get("content", "")
                elif "content" in result_data:
                    output_text = result_data["content"]

                return ExecutionResult(
                    success=True,
                    output=output_text,
                    metadata={
                        "elapsed_seconds": elapsed,
                        "usage": result_data.get("usage", {}),
                        "model": result_data.get("model"),
                    },
                )

        except asyncio.TimeoutError:
            return ExecutionResult(
                success=False,
                error=f"Request timeout after {self.config.timeout_seconds}s",
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"HTTPAgentRuntime execution failed: {e}")
            return ExecutionResult(
                success=False,
                error=f"{type(e).__name__}: {e}",
            )
        finally:
            self._current_request_ctx = None

    async def _stream_core(
        self, task_prompt: str, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        """流式执行任务（SSE）。

        Args:
            task_prompt: 任务提示词
            **kwargs: 额外参数

        Yields:
            SSE 事件 dict
        """
        if self._session is None:
            raise RuntimeError("HTTP session not initialized")

        # 构建请求体
        payload = {
            "session_id": self.session_id,
            "messages": [
                {"role": "user", "content": task_prompt}
            ],
            "stream": True,
            "model": self.config.model_config.get("model") if self.config.model_config else None,
            "temperature": kwargs.get("temperature", 0.7),
        }

        try:
            async with self._session.post(
                f"{self.base_url}/chat/stream",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self.config.timeout_seconds),
            ) as resp:
                self._current_request_ctx = resp

                if resp.status != 200:
                    error_text = await resp.text()
                    yield {"type": "error", "message": f"HTTP {resp.status}: {error_text}"}
                    return

                # 解析 SSE 流
                async for line in resp.content.iter_lines():
                    if self._cancel_flag:
                        break

                    if not line:
                        continue

                    line_str = line.decode("utf-8").strip()

                    if line_str.startswith("data: "):
                        data_str = line_str[6:]

                        if data_str.strip() == "[DONE]":
                            break

                        try:
                            data = json.loads(data_str)
                            sse_event = self._parse_sse_data(data)
                            if sse_event:
                                yield sse_event
                        except json.JSONDecodeError:
                            logger.warning(f"Invalid SSE data: {data_str}")

        except asyncio.TimeoutError:
            yield {"type": "error", "message": f"Stream timeout after {self.config.timeout_seconds}s"}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"HTTPAgentRuntime stream failed: {e}")
            yield {"type": "error", "message": f"{type(e).__name__}: {e}"}
        finally:
            self._current_request_ctx = None

    def _parse_sse_data(self, data: dict[str, Any]) -> dict[str, Any] | None:
        """解析 SSE 数据块。

        Args:
            data: SSE 数据 dict

        Returns:
            标准化的 SSE 事件 dict
        """
        # OpenAI 兼容格式
        if "choices" in data and data["choices"]:
            delta = data["choices"][0].get("delta", {})

            if "content" in delta and delta["content"]:
                return {"type": "text_delta", "delta": delta["content"]}

            if "tool_calls" in delta and delta["tool_calls"]:
                tool_call = delta["tool_calls"][0]
                return {
                    "type": "tool_start",
                    "id": tool_call.get("id", ""),
                    "name": tool_call.get("function", {}).get("name", ""),
                    "input": tool_call.get("function", {}).get("arguments", {}),
                }

        # 通用格式
        if "type" in data:
            return data

        if "content" in data:
            return {"type": "text_delta", "delta": data["content"]}

        return None

    async def _cleanup(self) -> None:
        """清理 HTTP 会话。"""
        if self._session:
            await self._session.close()
            self._session = None
        logger.debug(f"HTTPAgentRuntime cleanup for session {self.session_id}")

    async def _cancel(self) -> None:
        """取消当前请求。

        设置取消标志并关闭当前 HTTP 连接。
        """
        self._cancel_flag = True

        if self._current_request_ctx:
            try:
                await self._current_request_ctx.close()
            except Exception:
                pass

        # 尝试调用远程取消端点
        if self._session:
            try:
                await self._session.post(
                    f"{self.base_url}/cancel",
                    json={"session_id": self.session_id},
                    timeout=aiohttp.ClientTimeout(total=5),
                )
            except Exception:
                pass  # 忽略取消端点失败

        logger.info(f"HTTPAgentRuntime cancelled for session {self.session_id}")

    # ── HTTPAgentRuntime 特有方法 ───────────────────────────

    async def health_check(self) -> dict[str, Any]:
        """检查远程服务健康状态。

        Returns:
            健康检查结果 dict
        """
        if self._session is None:
            return {"healthy": False, "error": "Session not initialized"}

        try:
            async with self._session.get(
                f"{self.base_url}/health",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {"healthy": True, **data}
                return {"healthy": False, "status": resp.status}
        except Exception as e:
            return {"healthy": False, "error": str(e)}
