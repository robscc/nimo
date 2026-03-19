"""单元测试 — ProviderManager + RetryChatModel。"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpal.providers.manager import ProviderManager
from agentpal.providers.openai_provider import OpenAIProvider
from agentpal.providers.provider import ModelInfo, ProviderConfig
from agentpal.providers.retry_model import RetryChatModel, _backoff, _is_retryable


# ── ProviderManager 基础 ──────────────────────────────────────────────────

class TestProviderManager:
    def _mgr(self, tmp_path: Path) -> ProviderManager:
        ProviderManager.reset_instance()
        return ProviderManager(storage_dir=tmp_path)

    def test_builtin_providers_exist(self, tmp_path):
        mgr = self._mgr(tmp_path)
        for pid in ["dashscope", "openai", "compatible", "aliyun-codingplan", "ollama"]:
            assert mgr.get_provider(pid) is not None, f"Missing builtin: {pid}"

    def test_list_providers_masked(self, tmp_path):
        mgr = self._mgr(tmp_path)
        # api_key 不再通过 Provider 持久化，手动设置到内存中测试 masking
        provider = mgr.get_provider("openai")
        provider.api_key = "sk-secret12345"
        infos = mgr.list_providers(masked=True)
        openai_info = next(p for p in infos if p.id == "openai")
        assert "secret" not in openai_info.api_key
        assert openai_info.api_key.endswith("******")

    def test_list_providers_unmasked(self, tmp_path):
        mgr = self._mgr(tmp_path)
        # api_key 不再通过 Provider 持久化，手动设置到内存中测试
        provider = mgr.get_provider("openai")
        provider.api_key = "sk-abc"
        infos = mgr.list_providers(masked=False)
        openai_info = next(p for p in infos if p.id == "openai")
        assert openai_info.api_key == "sk-abc"

    def test_update_provider_persists_without_api_key(self, tmp_path):
        """Provider 持久化不再包含 api_key。"""
        mgr = self._mgr(tmp_path)
        mgr.update_provider("dashscope", {"generate_kwargs": {"temperature": 0.5}})

        # 重新创建，验证从磁盘恢复 generate_kwargs 但不恢复 api_key
        mgr2 = ProviderManager(storage_dir=tmp_path)
        p = mgr2.get_provider("dashscope")
        assert p.api_key == ""  # api_key 不从 JSON 恢复
        assert p.generate_kwargs.get("temperature") == 0.5

    def test_update_nonexistent_provider_returns_false(self, tmp_path):
        mgr = self._mgr(tmp_path)
        result = mgr.update_provider("nonexistent", {"api_key": "x"})
        assert result is False

    def test_freeze_url_cannot_be_overridden(self, tmp_path):
        mgr = self._mgr(tmp_path)
        provider = mgr.get_provider("dashscope")
        original_url = provider.base_url
        mgr.update_provider("dashscope", {"base_url": "https://evil.com"})
        assert provider.base_url == original_url  # freeze_url=True，不可修改

    def test_get_chat_model_returns_retry_wrapper(self, tmp_path):
        mgr = self._mgr(tmp_path)
        model = mgr.get_chat_model(
            "compatible", "test-model", stream=False,
            api_key_override="sk-x", base_url_override="http://localhost/v1",
        )
        assert isinstance(model, RetryChatModel)
        assert model.model_name == "test-model"
        assert model.inner_class.__name__ == "OpenAIChatModel"

    def test_get_chat_model_unknown_provider_fallback(self, tmp_path):
        mgr = self._mgr(tmp_path)
        model = mgr.get_chat_model(
            "unknown-xyz", "some-model", stream=False,
            api_key_override="sk-dummy", base_url_override="http://localhost/v1",
        )
        assert isinstance(model, RetryChatModel)  # 不抛异常，优雅 fallback

    def test_get_chat_model_missing_api_key_raises(self, tmp_path):
        """Provider 需要 API key 但 config.yaml 也没有时应 fail fast。"""
        mgr = self._mgr(tmp_path)
        # 确保 _get_config_yaml_api_key 返回 None（没有 config.yaml）
        with patch.object(ProviderManager, "_get_config_yaml_api_key", return_value=None):
            with pytest.raises(ValueError, match="需要 API key"):
                mgr.get_chat_model("dashscope", "qwen-max", stream=False)

    @pytest.mark.asyncio
    async def test_add_and_remove_custom_provider(self, tmp_path):
        mgr = self._mgr(tmp_path)
        config = ProviderConfig(
            id="my-provider",
            name="My Provider",
            base_url="https://my.api/v1",
            api_key="sk-xyz",
        )
        result = await mgr.add_custom_provider(config)
        assert result.id == "my-provider"
        assert mgr.get_provider("my-provider") is not None

        ok = mgr.remove_custom_provider("my-provider")
        assert ok is True
        assert mgr.get_provider("my-provider") is None

    @pytest.mark.asyncio
    async def test_add_custom_provider_id_conflict_resolved(self, tmp_path):
        mgr = self._mgr(tmp_path)
        # "dashscope" 是内置，应自动改名
        config = ProviderConfig(id="dashscope", name="My DashScope", base_url="https://x/v1")
        result = await mgr.add_custom_provider(config)
        assert result.id != "dashscope"
        assert "dashscope" in result.id  # 仍含原始名

    @pytest.mark.asyncio
    async def test_test_connection_nonexistent_provider(self, tmp_path):
        mgr = self._mgr(tmp_path)
        ok, msg = await mgr.test_connection("does-not-exist")
        assert ok is False
        assert "不存在" in msg

    @pytest.mark.asyncio
    async def test_fetch_provider_models_updates_extra_models(self, tmp_path):
        mgr = self._mgr(tmp_path)
        mock_models = [ModelInfo(id="gpt-99", name="GPT-99")]
        # Pydantic 实例不允许 setattr 方法，改为在 class 层面 patch
        with patch.object(OpenAIProvider, "fetch_models", AsyncMock(return_value=mock_models)):
            models = await mgr.fetch_provider_models("compatible")
        assert any(m.id == "gpt-99" for m in models)
        assert any(m.id == "gpt-99" for m in mgr.get_provider("compatible").extra_models)

    def test_singleton(self, tmp_path):
        ProviderManager.reset_instance()
        mgr1 = ProviderManager.get_instance()
        mgr2 = ProviderManager.get_instance()
        assert mgr1 is mgr2
        ProviderManager.reset_instance()


# ── RetryChatModel ────────────────────────────────────────────────────────

class TestRetryChatModel:
    def _make_inner(self, *, stream: bool = False, model_name: str = "test-model") -> AsyncMock:
        """创建 inner 模型的 AsyncMock（模拟 agentscope ChatModelBase）。"""
        inner = AsyncMock()
        inner.model_name = model_name
        inner.stream = stream
        # 给一个可区分的类型，用于 inner_class 断言
        inner.__class__ = type("FakeChatModel", (), {})
        return inner

    def test_wraps_inner_model(self):
        inner = self._make_inner()
        model = RetryChatModel(inner)
        assert model.model_name == "test-model"
        assert model.inner_class is inner.__class__

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        inner = self._make_inner()
        fake_response = MagicMock(content=[{"type": "text", "text": "hi"}])
        inner.return_value = fake_response  # await inner(...) → fake_response

        model = RetryChatModel(inner)
        result = await model([{"role": "user", "content": "hello"}])
        assert result is fake_response
        assert inner.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_retryable_error(self):
        try:
            import openai
        except ImportError:
            pytest.skip("openai not installed")

        inner = self._make_inner()
        fake_response = MagicMock(content=[])
        inner.side_effect = [
            openai.RateLimitError("rate limit", response=MagicMock(status_code=429), body={}),
            fake_response,
        ]

        model = RetryChatModel(inner)
        with patch("agentpal.providers.retry_model.asyncio.sleep", new_callable=AsyncMock):
            result = await model([])
        assert result is fake_response
        assert inner.call_count == 2

    @pytest.mark.asyncio
    async def test_does_not_retry_non_retryable_error(self):
        inner = self._make_inner()
        inner.side_effect = ValueError("bad input")

        model = RetryChatModel(inner)
        with pytest.raises(ValueError, match="bad input"):
            await model([])
        assert inner.call_count == 1

    @pytest.mark.asyncio
    async def test_exhausts_retries_then_raises(self):
        try:
            import openai
        except ImportError:
            pytest.skip("openai not installed")

        inner = self._make_inner()
        inner.side_effect = openai.RateLimitError(
            "rate limit", response=MagicMock(status_code=429), body={}
        )

        model = RetryChatModel(inner)
        with patch("agentpal.providers.retry_model.asyncio.sleep", new_callable=AsyncMock):
            with patch("agentpal.providers.retry_model.LLM_MAX_RETRIES", 2):
                with pytest.raises(openai.RateLimitError):
                    await model([])


# ── _backoff / _is_retryable ──────────────────────────────────────────────

class TestRetryHelpers:
    def test_backoff_grows_exponentially(self):
        b1 = _backoff(1)
        b2 = _backoff(2)
        b3 = _backoff(3)
        assert b1 < b2 < b3

    def test_backoff_capped(self):
        assert _backoff(100) <= 10.0  # LLM_BACKOFF_CAP default

    def test_is_retryable_status_code(self):
        exc = Exception()
        exc.status_code = 429  # type: ignore[attr-defined]
        assert _is_retryable(exc) is True

    def test_non_retryable_status_code(self):
        exc = Exception()
        exc.status_code = 400  # type: ignore[attr-defined]
        assert _is_retryable(exc) is False

    def test_non_retryable_generic_exception(self):
        assert _is_retryable(ValueError("bad")) is False
