"""OpenAI-compatible Provider（含 DashScope / Aliyun 端点）。"""

from __future__ import annotations

from typing import List

from agentscope.model import ChatModelBase, OpenAIChatModel

from agentpal.providers.provider import ModelInfo, Provider


class OpenAIProvider(Provider):
    """适用于 OpenAI API 及所有 OpenAI-compatible 端点。

    覆盖场景：
    - OpenAI 官方 (api.openai.com)
    - DashScope compatible mode
    - Aliyun Coding Plan
    - 自定义 OpenAI-compat 端点（本地 vLLM / LM Studio 等）
    """

    def _client(self, timeout: float = 5):
        import httpx
        from openai import AsyncOpenAI

        return AsyncOpenAI(
            base_url=self.base_url or None,
            api_key=self.api_key or "sk-dummy",
            timeout=timeout,
            http_client=httpx.AsyncClient(transport=httpx.AsyncHTTPTransport()),
        )

    async def check_connection(self, timeout: float = 5) -> tuple[bool, str]:
        try:
            client = self._client(timeout=timeout)
            await client.models.list(timeout=timeout)
            return True, ""
        except Exception as exc:
            return False, str(exc)

    async def fetch_models(self, timeout: float = 5) -> List[ModelInfo]:
        try:
            client = self._client(timeout=timeout)
            payload = await client.models.list(timeout=timeout)
            seen: set[str] = set()
            result: list[ModelInfo] = []
            for row in getattr(payload, "data", []) or []:
                mid = str(getattr(row, "id", "") or "").strip()
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                result.append(ModelInfo(id=mid, name=mid))
            return result
        except Exception:
            return []

    def get_chat_model_instance(
        self,
        model_id: str,
        stream: bool = False,
        api_key_override: str | None = None,
        base_url_override: str | None = None,
    ) -> ChatModelBase:
        api_key = api_key_override or self.api_key
        base_url = base_url_override or self.base_url

        # Fail fast：需要 API key 但未配置时，给出明确错误而非等到 401
        if self.require_api_key and not api_key:
            raise ValueError(
                f"Provider '{self.id}' 需要 API key 但未配置。"
                f" 请在 ~/.nimo/config.yaml 的 llm.api_key 中设置，"
                f" 或通过前端「设置」页面为 Provider '{self.name}' 配置 API key。"
            )

        client_kwargs: dict = {}
        if base_url:
            client_kwargs["base_url"] = base_url
        # 禁用系统代理（macOS 系统代理可能不可用），直连内网/自定义端点
        import httpx
        client_kwargs["http_client"] = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport())

        return OpenAIChatModel(
            model_name=model_id,
            api_key=api_key,
            stream=stream,
            client_kwargs=client_kwargs or None,
            generate_kwargs=self.generate_kwargs or None,
        )
