"""LLM 相关辅助函数 — 从 personal_assistant.py 提取的模块级 helper。"""

from __future__ import annotations

from typing import Any


def _build_user_message(user_input: str, images: list[str] | None = None) -> dict[str, Any]:
    """构建用户消息，支持多模态图片（OpenAI Vision 格式）。"""
    if images:
        content: list[dict[str, Any]] = [{"type": "text", "text": user_input}]
        for img in images:
            content.append({"type": "image_url", "image_url": {"url": img}})
        return {"role": "user", "content": content}
    return {"role": "user", "content": user_input}


def _rebuild_multimodal(msg_dict: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """如果消息有 images metadata，将 content 重建为多模态格式。"""
    images = meta.get("images")
    file_ids = meta.get("file_ids")
    attachment_context = meta.get("attachment_context")
    if msg_dict.get("role") == "user" and images:
        base_text = msg_dict["content"]
        if file_ids and attachment_context and isinstance(base_text, str):
            base_text = f"{base_text}\n\n{attachment_context}"
        content: list[dict[str, Any]] = [{"type": "text", "text": base_text}]
        for img in images:
            content.append({"type": "image_url", "image_url": {"url": img}})
        msg_dict["content"] = content
    return msg_dict


def _get_tool_names(toolkit: Any) -> list[str]:
    """从 toolkit 中提取工具名称列表。"""
    if toolkit is None:
        return []
    try:
        tools = getattr(toolkit, "tools", None)
        if isinstance(tools, dict):
            return list(tools.keys())
        return []
    except Exception:
        return []


def _default_model_config() -> dict[str, Any]:
    """每次调用都从 config.yaml 读取 LLM 配置（不走缓存）。

    这样修改 config.yaml 后无需重启即可生效。
    """
    from agentpal.services.config_file import ConfigFileManager

    cfg = ConfigFileManager().load()
    llm = cfg.get("llm", {})
    return {
        "provider": llm.get("provider", "dashscope"),
        "model_name": llm.get("model", "qwen-max"),
        "api_key": llm.get("api_key", ""),
        "base_url": llm.get("base_url", ""),
    }


def _build_model(
    config: dict[str, Any],
    stream: bool = False,
    on_retry: Any | None = None,
) -> Any:
    """根据 model_config 通过 ProviderManager 实例化模型（自动带重试）。

    config 字段：
        provider   — Provider ID（如 "dashscope"、"compatible"）
        model_name — 模型 ID（如 "qwen-max"）
        api_key    — 可选，Session 级覆盖（优先于 Provider 存储的 api_key）
        base_url   — 可选，Session 级覆盖（优先于 Provider 存储的 base_url）
    """
    from agentpal.providers.manager import ProviderManager

    provider_id = config.get("provider", "compatible")
    model_name = config.get("model_name", "")
    api_key_override = config.get("api_key") or None
    base_url_override = config.get("base_url") or None

    mgr = ProviderManager.get_instance()
    return mgr.get_chat_model(
        provider_id=provider_id,
        model_id=model_name,
        stream=stream,
        api_key_override=api_key_override,
        base_url_override=base_url_override,
        on_retry=on_retry,
    )


def _extract_text(response: Any) -> str:
    """从 agentscope 1.x ChatResponse 中提取纯文本。"""
    parts: list[str] = []
    for block in getattr(response, "content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "".join(parts) if parts else str(response)


def _extract_thinking(response: Any) -> tuple[str, str]:
    """从 ChatResponse.content 中提取 (thinking_text, answer_text)。

    agentscope 1.x 的 OpenAIChatModel 已将 reasoning_content 解析为
    ThinkingBlock(type="thinking", thinking=...)，直接从 content 读取即可。
    """
    thinking_parts: list[str] = []
    text_parts: list[str] = []
    for block in getattr(response, "content", []):
        if isinstance(block, dict):
            if block.get("type") == "thinking":
                thinking_parts.append(block.get("thinking", ""))
            elif block.get("type") == "text":
                text_parts.append(block.get("text", ""))
    thinking = "".join(thinking_parts).strip()
    text = "".join(text_parts).strip()
    if not text and not thinking_parts:
        text = str(response)
    return thinking, text
