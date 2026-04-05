"""内置工具定义 — 注册表入口。

实际工具实现已按领域拆分到子模块：
- builtin_fs.py      — 文件 / Shell 工具
- builtin_browser.py — 浏览器 / 网页交互
- builtin_cli.py     — skill_cli / cron_cli / plan_cli / 时间 / 文件发送
- builtin_agent.py   — SubAgent 派遣 / 代码执行 / 产出物

本文件负责：1) 汇总 re-export  2) 组装 BUILTIN_TOOLS 注册表
"""

from __future__ import annotations

# ── Re-exports（保持向后兼容） ────────────────────────────

from agentpal.tools.builtin_agent import (  # noqa: F401
    _get_scheduler,
    dispatch_sub_agent,
    execute_python_code,
    produce_artifact,
)
from agentpal.tools.builtin_browser import (  # noqa: F401
    USE_PLAYWRIGHT,
    _browser_use_httpx,
    _browser_use_playwright,
    _playwright_task,
    browser_use,
    sync_playwright,
)
from agentpal.tools.builtin_cli import (  # noqa: F401
    cron_cli,
    get_current_time,
    plan_cli,
    send_file_to_user,
    skill_cli,
)
from agentpal.tools.builtin_fs import (  # noqa: F401
    _text_response,
    edit_file,
    execute_shell_command,
    read_file,
    read_uploaded_file,
    write_file,
)

# ── 工具元数据注册表 ──────────────────────────────────────

BUILTIN_TOOLS: list[dict] = [
    {
        "name": "execute_shell_command",
        "func": execute_shell_command,
        "description": "执行 Shell 命令",
        "icon": "Terminal",
        "dangerous": True,
    },
    {
        "name": "read_file",
        "func": read_file,
        "description": "读取本地文件内容",
        "icon": "FileText",
        "dangerous": False,
    },
    {
        "name": "read_uploaded_file",
        "func": read_uploaded_file,
        "description": "读取聊天上传文件内容（受限目录）",
        "icon": "Paperclip",
        "dangerous": False,
    },
    {
        "name": "write_file",
        "func": write_file,
        "description": "写入文件（覆盖模式）",
        "icon": "FilePlus",
        "dangerous": True,
    },
    {
        "name": "edit_file",
        "func": edit_file,
        "description": "精确替换文件中的文本片段",
        "icon": "FileEdit",
        "dangerous": True,
    },
    {
        "name": "browser_use",
        "func": browser_use,
        "description": "访问网页并与之交互（支持 JS 渲染、点击、填表、截图等）",
        "icon": "Globe",
        "dangerous": False,
    },
    {
        "name": "send_file_to_user",
        "func": send_file_to_user,
        "description": "将本地文件发送给聊天界面中的用户（图片直接展示，其他提供下载）",
        "icon": "FileImage",
        "dangerous": False,
    },
    {
        "name": "get_current_time",
        "func": get_current_time,
        "description": "获取当前时间",
        "icon": "Clock",
        "dangerous": False,
    },
    {
        "name": "skill_cli",
        "func": skill_cli,
        "description": "管理技能包（列出、安装、启用、禁用、卸载）",
        "icon": "Puzzle",
        "dangerous": False,
    },
    {
        "name": "cron_cli",
        "func": cron_cli,
        "description": "管理定时任务（列出、创建、更新、删除、启用禁用、查看历史）",
        "icon": "Timer",
        "dangerous": False,
    },
    {
        "name": "plan_cli",
        "func": plan_cli,
        "description": "管理执行计划（列出、查看状态、取消计划）",
        "icon": "ClipboardList",
        "dangerous": False,
    },
    {
        "name": "execute_python_code",
        "func": execute_python_code,
        "description": "在独立子进程中动态执行 Python 代码，支持安装依赖包",
        "icon": "Code2",
        "dangerous": True,
    },
    {
        "name": "dispatch_sub_agent",
        "func": dispatch_sub_agent,
        "description": "将子任务委托给专业 SubAgent（coder/researcher）执行，支持阻塞/非阻塞模式",
        "icon": "Bot",
        "dangerous": False,
    },
    {
        "name": "produce_artifact",
        "func": produce_artifact,
        "description": "创建任务产出物（代码文件、报告、图表等），SubAgent 完成任务后调用此工具保存成果",
        "icon": "FileOutput",
        "dangerous": False,
        "subagent_only": True,  # 仅 SubAgent 可用
    },
]
