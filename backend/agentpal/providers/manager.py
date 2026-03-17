"""ProviderManager — LLM 供应商管理器（单例）。

职责：
- 维护内置 + 自定义 Provider 的注册表
- 将 Provider 配置（api_key、base_url 等）持久化到 ~/.nimo/providers/
- 提供统一入口 get_chat_model() 创建模型实例（自动套 RetryChatModel）
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from agentscope.model import ChatModelBase
from loguru import logger

from agentpal.providers.openai_provider import OpenAIProvider
from agentpal.providers.provider import ModelInfo, Provider, ProviderConfig
from agentpal.providers.retry_model import RetryCallback, RetryChatModel


# ── 内置 Provider 预设 ────────────────────────────────────────────────────

def _builtin_providers() -> list[Provider]:
    """返回所有内置 Provider 实例（含预置模型列表）。"""
    return [
        OpenAIProvider(
            id="aliyun-codingplan",
            name="Aliyun Coding Plan",
            base_url="https://coding.dashscope.aliyuncs.com/v1",
            freeze_url=True,
            models=[
                ModelInfo(id="qwen3.5-plus", name="Qwen3.5 Plus"),
                ModelInfo(id="qwen3-max-2026-01-23", name="Qwen3 Max"),
                ModelInfo(id="qwen3-coder-plus", name="Qwen3 Coder Plus"),
                ModelInfo(id="glm-5", name="GLM-5"),
                ModelInfo(id="MiniMax-M2.5", name="MiniMax M2.5"),
                ModelInfo(id="kimi-k2.5", name="Kimi K2.5"),
            ],
        ),
        OpenAIProvider(
            id="dashscope",
            name="DashScope",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            freeze_url=True,
            models=[
                ModelInfo(id="qwen3-max", name="Qwen3 Max"),
                ModelInfo(id="qwen3-235b-a22b-thinking-2507", name="Qwen3 235B Thinking"),
                ModelInfo(id="deepseek-v3.2", name="DeepSeek-V3.2"),
                ModelInfo(id="qwen-max", name="Qwen Max"),
                ModelInfo(id="qwen-plus", name="Qwen Plus"),
                ModelInfo(id="qwen-turbo", name="Qwen Turbo"),
            ],
        ),
        OpenAIProvider(
            id="openai",
            name="OpenAI",
            base_url="https://api.openai.com/v1",
            freeze_url=True,
            models=[
                ModelInfo(id="gpt-4.1", name="GPT-4.1"),
                ModelInfo(id="gpt-4.1-mini", name="GPT-4.1 Mini"),
                ModelInfo(id="gpt-4o", name="GPT-4o"),
                ModelInfo(id="gpt-4o-mini", name="GPT-4o Mini"),
                ModelInfo(id="o3", name="o3"),
                ModelInfo(id="o4-mini", name="o4-mini"),
            ],
        ),
        OpenAIProvider(
            id="compatible",
            name="OpenAI Compatible（自定义端点）",
            support_model_discovery=True,
        ),
        OpenAIProvider(
            id="ollama",
            name="Ollama（本地）",
            base_url="http://localhost:11434/v1",
            require_api_key=False,
            support_model_discovery=True,
            generate_kwargs={"max_tokens": None},
        ),
        OpenAIProvider(
            id="lmstudio",
            name="LM Studio（本地）",
            base_url="http://localhost:1234/v1",
            require_api_key=False,
            support_model_discovery=True,
            generate_kwargs={"max_tokens": None},
        ),
    ]


class ProviderManager:
    """LLM 供应商管理器（应用级单例）。

    Provider 配置持久化在 ~/.nimo/providers/builtin/<id>.json
    和 ~/.nimo/providers/custom/<id>.json。

    用法::

        mgr = ProviderManager.get_instance()
        model = mgr.get_chat_model("dashscope", "qwen-max", stream=True)
    """

    _instance: "ProviderManager | None" = None

    def __init__(self, storage_dir: Path | None = None) -> None:
        self._root = storage_dir or (Path.home() / ".nimo" / "providers")
        self._builtin_dir = self._root / "builtin"
        self._custom_dir = self._root / "custom"
        self._builtins: Dict[str, Provider] = {}
        self._customs: Dict[str, Provider] = {}
        self._prepare_dirs()
        self._init_builtins()
        self._load_from_storage()

    # ── 初始化 ────────────────────────────────────────────────────────────

    def _prepare_dirs(self) -> None:
        for d in [self._root, self._builtin_dir, self._custom_dir]:
            d.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(d, 0o700)
            except Exception:
                pass

    def _init_builtins(self) -> None:
        for p in _builtin_providers():
            self._builtins[p.id] = p

    def _load_from_storage(self) -> None:
        """从磁盘恢复保存过的 api_key / base_url / extra_models 等。"""
        # 内置：只覆盖用户修改过的字段
        for pid, provider in self._builtins.items():
            saved = self._load_json(self._builtin_dir / f"{pid}.json")
            if saved:
                if saved.get("api_key"):
                    provider.api_key = saved["api_key"]
                if not provider.freeze_url and saved.get("base_url"):
                    provider.base_url = saved["base_url"]
                if isinstance(saved.get("generate_kwargs"), dict):
                    provider.generate_kwargs.update(saved["generate_kwargs"])
                if saved.get("extra_models"):
                    provider.extra_models = [
                        ModelInfo.model_validate(m) for m in saved["extra_models"]
                    ]
        # 自定义：完整恢复
        for path in self._custom_dir.glob("*.json"):
            data = self._load_json(path)
            if data:
                try:
                    p = OpenAIProvider.model_validate(data)
                    self._customs[p.id] = p
                except Exception as exc:
                    logger.warning("加载自定义 Provider 失败 %s：%s", path, exc)

    # ── 查询 ──────────────────────────────────────────────────────────────

    def get_provider(self, provider_id: str) -> Provider | None:
        return self._builtins.get(provider_id) or self._customs.get(provider_id)

    def list_providers(self, masked: bool = True) -> List[ProviderConfig]:
        all_providers = list(self._builtins.values()) + list(self._customs.values())
        if masked:
            return [p.public_info() for p in all_providers]
        return [ProviderConfig.model_validate(p.model_dump()) for p in all_providers]

    # ── 更新 ──────────────────────────────────────────────────────────────

    def update_provider(self, provider_id: str, data: Dict[str, Any]) -> bool:
        """更新 Provider 配置并持久化。"""
        provider = self.get_provider(provider_id)
        if not provider:
            return False
        provider.update_config(data)
        is_builtin = provider_id in self._builtins
        self._save_provider(provider, is_builtin=is_builtin)
        return True

    async def add_custom_provider(self, config: ProviderConfig) -> ProviderConfig:
        """添加自定义 Provider。"""
        pid = self._resolve_id(config.id)
        payload = config.model_dump()
        payload["id"] = pid
        payload["is_custom"] = True
        provider = OpenAIProvider.model_validate(payload)
        self._customs[pid] = provider
        self._save_provider(provider, is_builtin=False)
        return provider.public_info()

    def remove_custom_provider(self, provider_id: str) -> bool:
        """删除自定义 Provider。"""
        if provider_id not in self._customs:
            return False
        del self._customs[provider_id]
        path = self._custom_dir / f"{provider_id}.json"
        if path.exists():
            path.unlink()
        return True

    # ── 模型列表 ──────────────────────────────────────────────────────────

    async def fetch_provider_models(self, provider_id: str) -> List[ModelInfo]:
        """从 Provider API 动态拉取模型列表并缓存到 extra_models。"""
        provider = self.get_provider(provider_id)
        if not provider:
            return []
        try:
            models = await provider.fetch_models()
            provider.extra_models = models
            self._save_provider(provider, is_builtin=provider_id in self._builtins)
            return models
        except Exception as exc:
            logger.warning("拉取 %s 模型列表失败：%s", provider_id, exc)
            return []

    # ── 模型实例化（核心入口）────────────────────────────────────────────

    def get_chat_model(
        self,
        provider_id: str,
        model_id: str,
        stream: bool = False,
        api_key_override: str | None = None,
        base_url_override: str | None = None,
        on_retry: "RetryCallback | None" = None,
    ) -> ChatModelBase:
        """构建模型实例，自动套 RetryChatModel 包装。

        Args:
            provider_id:       Provider ID（如 "dashscope"、"compatible"）
            model_id:          模型 ID（如 "qwen-max"）
            stream:            是否流式
            api_key_override:  优先使用此 api_key（来自 session 级配置）
            base_url_override: 优先使用此 base_url（来自 session 级配置）
            on_retry:          重试回调，签名 (attempt, max_attempts, error, delay)
        """
        provider = self.get_provider(provider_id)
        if provider is None:
            # fallback：动态构建一个 OpenAIProvider
            logger.warning("未知 Provider '%s'，使用 OpenAI-compat 模式回退", provider_id)
            provider = OpenAIProvider(
                id=provider_id,
                name=provider_id,
                base_url=base_url_override or "",
                api_key=api_key_override or "",
            )

        inner = provider.get_chat_model_instance(
            model_id=model_id,
            stream=stream,
            api_key_override=api_key_override,
            base_url_override=base_url_override,
        )
        return RetryChatModel(inner, on_retry=on_retry)

    # ── 连接测试 ──────────────────────────────────────────────────────────

    async def test_connection(self, provider_id: str) -> tuple[bool, str]:
        provider = self.get_provider(provider_id)
        if not provider:
            return False, f"Provider '{provider_id}' 不存在"
        return await provider.check_connection()

    # ── 持久化工具 ────────────────────────────────────────────────────────

    def _save_provider(self, provider: Provider, is_builtin: bool) -> None:
        path = (self._builtin_dir if is_builtin else self._custom_dir) / f"{provider.id}.json"
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(provider.model_dump(), f, ensure_ascii=False, indent=2)
            os.chmod(path, 0o600)
        except Exception as exc:
            logger.warning("保存 Provider '%s' 失败：%s", provider.id, exc)

    @staticmethod
    def _load_json(path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _resolve_id(self, proposed: str) -> str:
        """解决自定义 Provider ID 冲突。"""
        pid = proposed
        if pid in self._builtins:
            pid = f"{pid}-custom"
        while pid in self._builtins or pid in self._customs:
            pid = f"{pid}-new"
        return pid

    # ── 单例 ──────────────────────────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> "ProviderManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """测试用：重置单例。"""
        cls._instance = None
