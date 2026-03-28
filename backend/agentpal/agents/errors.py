"""SubAgent 错误类型定义 — 区分可重试（transient）与不可重试（permanent）错误。

SubAgent.run() 根据异常的 retryable 属性决定是否进行任务级重试：
- retryable=True  → 指数退避重试，耗尽后 FAILED
- retryable=False → 直接标记 FAILED，不浪费额度
"""

from __future__ import annotations


class SubAgentError(Exception):
    """SubAgent 操作基础异常。"""

    retryable: bool = True


class LLMResponseError(SubAgentError):
    """LLM 返回了无效响应的基础异常。"""

    retryable = True


class LLMEmptyResponseError(LLMResponseError):
    """LLM 返回了空内容（None、空列表或无有效 block）。"""

    retryable = True


class PermanentLLMError(SubAgentError):
    """不可重试的 LLM 错误（认证失败、请求格式错误、配置错误等）。"""

    retryable = False
