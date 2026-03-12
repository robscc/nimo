"""Hello World 示例技能 — 展示如何编写 AgentPal Skill。

每个工具函数：
- 接受 Python 原生类型参数
- 返回 agentscope.tool.ToolResponse
- 函数签名的 docstring 即文档（agentscope 自动生成 JSON Schema）
"""

import random

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse


def say_hello(name: str = "世界") -> ToolResponse:
    """向指定用户打招呼。

    Args:
        name: 用户名称，默认为"世界"

    Returns:
        友好的问候语
    """
    greetings = [
        f"你好，{name}！很高兴见到你 😊",
        f"嗨，{name}！今天过得怎么样？",
        f"Hello, {name}! Nice to meet you! 🎉",
        f"{name}，欢迎来到 AgentPal！",
    ]
    return ToolResponse(
        content=[TextBlock(type="text", text=random.choice(greetings))]
    )


def tell_joke() -> ToolResponse:
    """讲一个随机笑话。

    Returns:
        一个有趣的笑话
    """
    jokes = [
        "为什么程序员总是搞混万圣节和圣诞节？因为 Oct 31 == Dec 25！",
        "一个 SQL 语句走进一家酒吧，看到两张桌子，问：'我可以 JOIN 你们吗？'",
        "为什么 Python 程序员戴眼镜？因为他们看不见 C！",
        "程序员最怕什么？改需求。比这更怕的是什么？需求没变但代码不能用了。",
        "有个程序员去面试，面试官问他：'你有什么缺点？'他说：'我太诚实了。'面试官说：'我觉得这不算缺点。'他说：'我不在乎你怎么想。'",
    ]
    return ToolResponse(
        content=[TextBlock(type="text", text=random.choice(jokes))]
    )
