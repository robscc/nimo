"""CLI 桥接工具 — skill_cli / cron_cli / plan_cli / get_current_time / send_file_to_user。"""

from __future__ import annotations

import json
import mimetypes
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentscope.tool import ToolResponse

from agentpal.config import get_settings
from agentpal.tools.builtin_fs import _text_response


# ── 6. get_current_time ───────────────────────────────────


def get_current_time(timezone_name: str = "Asia/Shanghai") -> ToolResponse:
    """获取当前时间。

    Args:
        timezone_name: 时区名称，默认 Asia/Shanghai（北京时间）

    Returns:
        当前日期和时间字符串
    """
    try:
        import zoneinfo
        tz = zoneinfo.ZoneInfo(timezone_name)
        now = datetime.now(tz)
        result = (
            f"当前时间（{timezone_name}）\n"
            f"日期: {now.strftime('%Y年%m月%d日 %A')}\n"
            f"时间: {now.strftime('%H:%M:%S')}\n"
            f"ISO: {now.isoformat()}"
        )
        return _text_response(result)
    except Exception as e:
        # 回退到 UTC
        now = datetime.now(timezone.utc)
        return _text_response(f"当前 UTC 时间: {now.isoformat()} （时区解析失败: {e}）")


# ── 7. send_file_to_user ─────────────────────────────────

_UPLOADS_DIR = Path("uploads")


def send_file_to_user(file_path: str, filename: str = "") -> ToolResponse:
    """将本地文件发送给聊天界面中的用户（图片直接展示，其他文件提供下载链接）。

    Args:
        file_path: 本地文件绝对路径（如截图 /tmp/agentpal_screenshot_123.png）
        filename:  显示给用户的文件名（可选，默认使用原文件名）

    Returns:
        包含文件公开访问 URL 的 JSON 结果
    """
    try:
        src = Path(file_path).expanduser()
        if not src.exists():
            return _text_response(f"<error>文件不存在: {file_path}</error>")

        name = filename.strip() if filename.strip() else src.name
        _UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        dest = _UPLOADS_DIR / name
        shutil.copy2(src, dest)

        mime, _ = mimetypes.guess_type(name)
        mime = mime or "application/octet-stream"

        payload = json.dumps(
            {"status": "sent", "url": f"/uploads/{name}", "filename": name, "mime": mime},
            ensure_ascii=False,
        )
        return _text_response(payload)
    except Exception as e:
        return _text_response(f"<error>{e}</error>")


# ── 8. skill_cli ─────────────────────────────────────────


def skill_cli(action: str, name: str = "", url: str = "") -> ToolResponse:
    """管理 AgentPal 技能包的命令行工具。

    支持的操作：
    - list: 列出所有已安装技能
    - enable <name>: 启用指定技能
    - disable <name>: 禁用指定技能
    - remove <name>: 卸载指定技能
    - install <url>: 从 URL 安装技能（支持 skills.sh / clawhub.ai / 直接 ZIP）
    - search <name>: 在 skills.sh 上搜索技能

    Args:
        action: 操作类型，可选值: list, enable, disable, remove, install, search
        name: 技能名称（用于 enable/disable/remove/search）
        url: 技能包 URL（用于 install，支持 skills.sh 如 https://skills.sh/org/repo/skill-name）

    Returns:
        操作结果文本
    """
    import asyncio
    import concurrent.futures

    async def _run() -> str:
        from agentpal.database import AsyncSessionLocal
        from agentpal.skills.manager import SkillManager

        async with AsyncSessionLocal() as db:
            mgr = SkillManager(db)

            if action == "list":
                skills = await mgr.list_skills()
                if not skills:
                    return (
                        "当前没有已安装的技能。\n"
                        "提示：可以用 skill_cli(action='install', url='https://skills.sh/...') 安装技能。"
                    )
                lines = []
                for s in skills:
                    status = "✅" if s.get("enabled") else "❌"
                    stype = f" [{s.get('skill_type', 'python')}]" if s.get("skill_type") == "prompt" else ""
                    lines.append(f"  {status} {s['name']} v{s.get('version','?')}{stype} — {s.get('description','')[:60]}")
                return f"已安装技能（{len(skills)} 个）:\n" + "\n".join(lines)

            elif action == "install":
                if not url:
                    return "<error>install 操作需要提供 url 参数</error>"
                result = await mgr.install_from_url(url)
                await db.commit()
                skill_type = result.get("skill_type", "python")
                type_label = "prompt 型技能（注入系统知识）" if skill_type == "prompt" else f"包含 {len(result.get('tools', []))} 个工具"
                return (
                    f"✅ 技能安装成功！\n"
                    f"  名称: {result['name']}\n"
                    f"  版本: {result.get('version', '?')}\n"
                    f"  描述: {result.get('description', '')}\n"
                    f"  类型: {type_label}"
                )

            elif action == "enable":
                if not name:
                    return "<error>enable 操作需要提供 name 参数</error>"
                ok = await mgr.enable(name)
                if ok:
                    await db.commit()
                    return f"✅ 技能 {name!r} 已启用"
                return f"<error>技能 {name!r} 不存在</error>"

            elif action == "disable":
                if not name:
                    return "<error>disable 操作需要提供 name 参数</error>"
                ok = await mgr.disable(name)
                if ok:
                    await db.commit()
                    return f"✅ 技能 {name!r} 已禁用"
                return f"<error>技能 {name!r} 不存在</error>"

            elif action == "remove":
                if not name:
                    return "<error>remove 操作需要提供 name 参数</error>"
                ok = await mgr.uninstall(name)
                if ok:
                    await db.commit()
                    return f"✅ 技能 {name!r} 已卸载"
                return f"<error>技能 {name!r} 不存在</error>"

            elif action == "search":
                query = name or url or ""
                if not query:
                    return (
                        "请提供搜索关键词。\n"
                        "示例: skill_cli(action='search', name='frontend design')\n\n"
                        "也可以直接浏览 https://skills.sh/ 查找技能。"
                    )
                return (
                    f"搜索关键词: {query}\n\n"
                    f"请访问 https://skills.sh/ 搜索相关技能，然后用以下方式安装：\n"
                    f"  skill_cli(action='install', url='https://skills.sh/<org>/<repo>/<skill-name>')\n\n"
                    f"热门技能仓库：\n"
                    f"  - vercel-labs/agent-skills — React、Web 设计等\n"
                    f"  - anthropics/skills — 前端设计、skill 创建器\n"
                    f"  - vercel-labs/skills — find-skills 等基础技能"
                )

            else:
                return (
                    f"<error>不支持的操作: {action}</error>\n"
                    f"可用操作: list, install, enable, disable, remove, search"
                )

    def _thread_run() -> str:
        """在独立线程中创建新事件循环运行异步代码。"""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_thread_run)
            result_text = future.result(timeout=90)
        return _text_response(result_text)
    except Exception as e:
        return _text_response(f"<error>操作失败: {e}</error>")


# ── 9. cron_cli ──────────────────────────────────────────


def cron_cli(
    action: str,
    name: str = "",
    schedule: str = "",
    task_prompt: str = "",
    job_id: str = "",
    enabled: bool = True,
    target_session_id: str = "",
) -> ToolResponse:
    """管理 AgentPal 内置定时任务的命令行工具（使用系统内部调度器，非系统 crontab）。

    支持的操作：
    - list: 列出所有定时任务
    - create: 创建新定时任务（需 name、schedule、task_prompt）
    - update: 更新定时任务（需 job_id，可选 name、schedule、task_prompt）
    - delete: 删除定时任务（需 job_id）
    - toggle: 启用/禁用定时任务（需 job_id、enabled）
    - history: 查看执行历史（可选 job_id 按任务筛选）

    Args:
        action: 操作类型，可选值: list, create, update, delete, toggle, history
        name: 任务名称（用于 create/update）
        schedule: cron 表达式（用于 create/update），格式为标准 5 位 cron（分 时 日 月 周），例如 "0 9 * * *" 表示每天 9 点
        task_prompt: 任务执行时发送给 Agent 的提示词（用于 create/update）
        job_id: 任务 ID（用于 update/delete/toggle/history）
        enabled: 是否启用（用于 toggle，默认 True）
        target_session_id: 任务完成后将结果发送到的 session ID（可选）。
            如果用户在某个对话中要求创建定时任务，应将当前 session_id（见 Runtime Environment）
            作为此参数传入，这样任务结果会直接推送到当前对话界面。
            留空则通过 MessageBus 通知主 Agent（不推送到具体对话）。

    Returns:
        操作结果文本
    """
    import asyncio
    import concurrent.futures

    async def _run() -> str:
        from agentpal.database import AsyncSessionLocal
        from agentpal.services.cron_scheduler import CronManager

        async with AsyncSessionLocal() as db:
            mgr = CronManager(db)

            if action == "list":
                jobs = await mgr.list_jobs()
                if not jobs:
                    return (
                        "当前没有定时任务。\n"
                        "提示：可以用 cron_cli(action='create', name='任务名', schedule='0 9 * * *', task_prompt='...') 创建定时任务。\n\n"
                        "cron 表达式格式：分 时 日 月 周\n"
                        "  例: '0 9 * * *'   = 每天 09:00\n"
                        "  例: '*/30 * * * *' = 每 30 分钟\n"
                        "  例: '0 9 * * 1'   = 每周一 09:00"
                    )
                lines = []
                for j in jobs:
                    status = "✅" if j["enabled"] else "⏸️"
                    next_run = j.get("next_run_at", "未知")
                    lines.append(
                        f"  {status} {j['name']}\n"
                        f"     ID: {j['id']}\n"
                        f"     计划: {j['schedule']}  |  下次执行: {next_run}"
                    )
                return f"定时任务列表（{len(jobs)} 个）:\n\n" + "\n\n".join(lines)

            elif action == "create":
                if not name:
                    return "<error>create 操作需要提供 name 参数（任务名称）</error>"
                if not schedule:
                    return (
                        "<error>create 操作需要提供 schedule 参数（cron 表达式）</error>\n"
                        "cron 表达式格式：分 时 日 月 周\n"
                        "  例: '0 9 * * *'   = 每天 09:00\n"
                        "  例: '*/30 * * * *' = 每 30 分钟"
                    )
                if not task_prompt:
                    return "<error>create 操作需要提供 task_prompt 参数（任务执行时的提示词）</error>"

                try:
                    result = await mgr.create_job({
                        "name": name,
                        "schedule": schedule,
                        "task_prompt": task_prompt,
                        "enabled": enabled,
                        "target_session_id": target_session_id or None,
                    })
                    await db.commit()
                    session_hint = (
                        f"\n  通知会话: {result.get('target_session_id')}"
                        if result.get("target_session_id") else ""
                    )
                    return (
                        f"✅ 定时任务创建成功！\n"
                        f"  名称: {result['name']}\n"
                        f"  ID: {result['id']}\n"
                        f"  计划: {result['schedule']}\n"
                        f"  下次执行: {result.get('next_run_at', '计算中...')}\n"
                        f"  提示词: {result['task_prompt'][:100]}"
                        f"{session_hint}"
                    )
                except ValueError as e:
                    return f"<error>{e}</error>"

            elif action == "update":
                if not job_id:
                    return "<error>update 操作需要提供 job_id 参数</error>"
                update_data: dict[str, Any] = {}
                if name:
                    update_data["name"] = name
                if schedule:
                    update_data["schedule"] = schedule
                if task_prompt:
                    update_data["task_prompt"] = task_prompt
                if target_session_id:
                    update_data["target_session_id"] = target_session_id
                if not update_data:
                    return "<error>update 操作至少需要提供一个要更新的字段（name/schedule/task_prompt/target_session_id）</error>"

                try:
                    result = await mgr.update_job(job_id, update_data)
                    if result is None:
                        return f"<error>定时任务不存在: {job_id}</error>"
                    await db.commit()
                    return (
                        f"✅ 定时任务已更新\n"
                        f"  名称: {result['name']}\n"
                        f"  计划: {result['schedule']}\n"
                        f"  下次执行: {result.get('next_run_at', '计算中...')}"
                    )
                except ValueError as e:
                    return f"<error>{e}</error>"

            elif action == "delete":
                if not job_id:
                    return "<error>delete 操作需要提供 job_id 参数</error>"
                ok = await mgr.delete_job(job_id)
                if ok:
                    await db.commit()
                    return f"✅ 定时任务已删除: {job_id}"
                return f"<error>定时任务不存在: {job_id}</error>"

            elif action == "toggle":
                if not job_id:
                    return "<error>toggle 操作需要提供 job_id 参数</error>"
                result = await mgr.toggle_job(job_id, enabled)
                if result is None:
                    return f"<error>定时任务不存在: {job_id}</error>"
                await db.commit()
                state = "启用" if enabled else "禁用"
                return f"✅ 定时任务已{state}: {result['name']}"

            elif action == "history":
                executions = await mgr.list_executions(
                    cron_job_id=job_id if job_id else None, limit=20
                )
                if not executions:
                    return "暂无执行记录。"
                lines = []
                for ex in executions:
                    status_icon = {"done": "✅", "failed": "❌", "running": "⏳"}.get(
                        ex.get("status", ""), "❓"
                    )
                    result_text = ex.get("result", "") or ex.get("error", "") or ""
                    if len(result_text) > 100:
                        result_text = result_text[:100] + "..."
                    lines.append(
                        f"  {status_icon} {ex.get('cron_job_name', '?')} — {ex.get('started_at', '?')}\n"
                        f"     结果: {result_text or '(无)'}"
                    )
                return f"执行历史（最近 {len(executions)} 条）:\n\n" + "\n\n".join(lines)

            else:
                return (
                    f"<error>不支持的操作: {action}</error>\n"
                    f"可用操作: list, create, update, delete, toggle, history"
                )

    def _thread_run() -> str:
        """在独立线程中创建新事件循环运行异步代码。"""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_thread_run)
            result_text = future.result(timeout=30)
        return _text_response(result_text)
    except Exception as e:
        return _text_response(f"<error>操作失败: {e}</error>")


# ── 10a. plan_cli ──────────────────────────────────────────


def plan_cli(
    action: str,
    plan_id: str = "",
    session_id: str = "",
) -> ToolResponse:
    """管理 Plan Mode 执行计划（list / status / cancel）。

    该工具用于“计划管理”，而不是“生成计划”：
    - 生成/修改计划：由 Plan Mode 对话流程处理
    - 查询/取消计划：使用本工具

    ## 何时使用
    - 用户问“当前计划执行到哪一步了” → `action="status"`
    - 用户问“列出本会话所有计划” → `action="list"`
    - 用户要求“停止/取消当前计划” → `action="cancel"`

    ## 何时不要使用
    - 不要用它创建计划步骤（它不支持 create/update）
    - 不要在无 plan 上下文时调用 cancel

    Args:
        action: 操作类型，可选：
            - list: 列出会话下所有计划摘要
            - status: 查看某个计划详情；若未提供 plan_id，尝试返回当前活跃计划
            - cancel: 取消某个计划；若未提供 plan_id，取消当前活跃计划
        plan_id: 计划 ID（status/cancel 可选；空则尝试活跃计划）
        session_id: 会话 ID（必填，来自 Runtime Environment 的 session_id）

    Returns:
        文本结果，包含计划状态/进度/步骤摘要或错误信息。

    Examples:
        # 列出当前会话计划
        plan_cli(action="list", session_id="abc123")

        # 查看活跃计划详情
        plan_cli(action="status", session_id="abc123")

        # 查看指定计划详情
        plan_cli(action="status", session_id="abc123", plan_id="plan-001")

        # 取消活跃计划
        plan_cli(action="cancel", session_id="abc123")
    """
    import asyncio
    import concurrent.futures

    async def _run() -> str:
        from agentpal.plans.store import PlanStore

        settings = get_settings()
        store = PlanStore(settings.plans_dir)

        if action == "list":
            if not session_id:
                return "<error>list 操作需要提供 session_id 参数（见 Runtime Environment）</error>"
            plans = await store.list_plans(session_id)
            if not plans:
                return "当前没有执行计划。"
            lines = []
            for p in plans:
                status_icon = {
                    "generating": "🔄",
                    "confirming": "⏳",
                    "executing": "▶️",
                    "completed": "✅",
                    "cancelled": "⏹️",
                    "failed": "❌",
                }.get(p["status"], "❓")
                progress = f"{p['steps_completed']}/{p['steps_total']}"
                lines.append(
                    f"  {status_icon} {p['goal'][:50]}\n"
                    f"     ID: {p['id']}\n"
                    f"     状态: {p['status']}  |  进度: {progress}"
                )
            return f"执行计划列表（{len(plans)} 个）:\n\n" + "\n\n".join(lines)

        elif action == "status":
            if not plan_id:
                # 尝试获取活跃计划
                if not session_id:
                    return "<error>status 操作需要提供 plan_id 或 session_id 参数</error>"
                plan = await store.get_active(session_id)
                if plan is None:
                    return "当前没有活跃的执行计划。"
            else:
                if not session_id:
                    return "<error>status 操作需要同时提供 session_id 参数</error>"
                plan = await store.load(session_id, plan_id)
                if plan is None:
                    return f"<error>计划不存在: {plan_id}</error>"

            lines = [
                f"📋 计划详情\n",
                f"  ID: {plan.id}",
                f"  目标: {plan.goal}",
                f"  概述: {plan.summary}",
                f"  状态: {plan.status}",
                f"  步骤进度: {plan.current_step + 1}/{len(plan.steps)}",
                "",
            ]
            for s in plan.steps:
                icon = {
                    "pending": "⬜",
                    "running": "⏳",
                    "completed": "✅",
                    "failed": "❌",
                    "skipped": "⏭",
                }.get(s.status, "❓")
                line = f"  {icon} 步骤 {s.index + 1}: {s.title} [{s.status}]"
                if s.result:
                    result_preview = s.result[:100] + ("..." if len(s.result) > 100 else "")
                    line += f"\n     结果: {result_preview}"
                if s.error:
                    line += f"\n     错误: {s.error[:100]}"
                lines.append(line)
            return "\n".join(lines)

        elif action == "cancel":
            if not session_id:
                return "<error>cancel 操作需要提供 session_id 参数</error>"
            if plan_id:
                plan = await store.load(session_id, plan_id)
            else:
                plan = await store.get_active(session_id)

            if plan is None:
                return "<error>没有找到可取消的计划</error>"

            from agentpal.plans.store import PlanStatus

            if plan.status in (PlanStatus.COMPLETED, PlanStatus.CANCELLED, PlanStatus.FAILED):
                return f"计划已处于终态: {plan.status}"

            plan.status = PlanStatus.CANCELLED
            await store.save(plan)

            # 同步更新 session agent_mode
            from agentpal.database import AsyncSessionLocal
            from sqlalchemy import text as sa_text
            async with AsyncSessionLocal() as db:
                await db.execute(
                    sa_text("UPDATE sessions SET agent_mode = 'normal' WHERE id = :sid"),
                    {"sid": session_id},
                )
                await db.commit()

            return f"✅ 计划已取消: {plan.goal[:50]}"

        else:
            return (
                f"<error>不支持的操作: {action}</error>\n"
                f"可用操作: list, status, cancel"
            )

    def _thread_run() -> str:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_thread_run)
            result_text = future.result(timeout=30)
        return _text_response(result_text)
    except Exception as e:
        return _text_response(f"<error>操作失败: {e}</error>")
