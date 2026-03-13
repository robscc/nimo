"""内置工具定义 — 基于 agentscope.tool 实现，遵循 ToolResponse 协议。

每个工具：
- 接受 Python 原生类型参数
- 返回 agentscope.tool.ToolResponse（内含 TextBlock）
- 函数签名即文档，agentscope 自动生成 JSON Schema 供 LLM 调用
"""

from __future__ import annotations

import json
import mimetypes
import platform
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from agentpal.config import get_settings

# ── Playwright 可选导入（不存在时降级到 httpx）──────────────
try:
    from playwright.sync_api import sync_playwright

    USE_PLAYWRIGHT = True
except ImportError:
    USE_PLAYWRIGHT = False
    sync_playwright = None  # type: ignore[assignment]  # placeholder，供测试 mock

# ── 辅助函数 ──────────────────────────────────────────────


def _text_response(text: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=text)])


# ── 1. execute_shell_command ──────────────────────────────


def execute_shell_command(command: str, timeout: int = 30) -> ToolResponse:
    """执行 Shell 命令并返回输出结果。

    Args:
        command: 要执行的 shell 命令
        timeout: 超时秒数（默认 30 秒）

    Returns:
        包含 returncode、stdout、stderr 的执行结果
    """
    settings = get_settings()
    workspace = Path(settings.workspace_dir).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workspace,
        )
        output = (
            f"<returncode>{result.returncode}</returncode>\n"
            f"<stdout>{result.stdout.strip()}</stdout>\n"
            f"<stderr>{result.stderr.strip()}</stderr>"
        )
        return _text_response(output)
    except subprocess.TimeoutExpired:
        return _text_response(f"<error>命令超时（{timeout}秒）</error>")
    except Exception as e:
        return _text_response(f"<error>{e}</error>")


# ── 2. read_file ──────────────────────────────────────────


def read_file(file_path: str, start_line: int = 1, end_line: int | None = None) -> ToolResponse:
    """读取文件内容。

    Args:
        file_path: 文件路径（绝对路径或相对路径）
        start_line: 起始行号（从 1 开始，默认 1）
        end_line: 结束行号（默认读到文件末尾）

    Returns:
        文件内容文本
    """
    try:
        path = Path(file_path).expanduser()
        if not path.exists():
            return _text_response(f"<error>文件不存在: {file_path}</error>")
        if path.stat().st_size > 1024 * 1024:  # 1MB 限制
            return _text_response("<error>文件过大（超过 1MB），请指定行范围</error>")

        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        selected = lines[start_line - 1 : end_line]
        numbered = "\n".join(f"{start_line + i:4d}│ {line}" for i, line in enumerate(selected))
        return _text_response(f"# {file_path}\n```\n{numbered}\n```")
    except Exception as e:
        return _text_response(f"<error>{e}</error>")


# ── 3. write_file ─────────────────────────────────────────


def write_file(file_path: str, content: str) -> ToolResponse:
    """将内容写入文件（覆盖模式）。

    Args:
        file_path: 目标文件路径
        content: 要写入的文本内容

    Returns:
        操作结果
    """
    try:
        path = Path(file_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return _text_response(f"✅ 已写入 {path}（{len(content)} 字符）")
    except Exception as e:
        return _text_response(f"<error>{e}</error>")


# ── 4. edit_file ──────────────────────────────────────────


def edit_file(file_path: str, old_text: str, new_text: str) -> ToolResponse:
    """精确替换文件中的指定文本片段。

    Args:
        file_path: 目标文件路径
        old_text: 要替换的原始文本（必须在文件中唯一存在）
        new_text: 替换后的新文本

    Returns:
        操作结果
    """
    try:
        path = Path(file_path).expanduser()
        if not path.exists():
            return _text_response(f"<error>文件不存在: {file_path}</error>")

        original = path.read_text(encoding="utf-8")
        count = original.count(old_text)
        if count == 0:
            return _text_response("<error>未找到指定文本，请检查 old_text 是否准确</error>")
        if count > 1:
            return _text_response(f"<error>找到 {count} 处匹配，old_text 必须唯一，请提供更多上下文</error>")

        updated = original.replace(old_text, new_text, 1)
        path.write_text(updated, encoding="utf-8")
        return _text_response(f"✅ 已完成替换（{file_path}）")
    except Exception as e:
        return _text_response(f"<error>{e}</error>")


# ── 5. browser_use ────────────────────────────────────────


def browser_use(
    url: str,
    action: str = "get_text",
    selector: str = "",
    value: str = "",
    distance: int = 800,
    wait_ms: int = 1500,
) -> ToolResponse:
    """访问网页并与之交互（基于 Playwright 无头浏览器，支持 JS 渲染）。

    Args:
        url: 要访问的网页 URL
        action: 操作类型，支持：
            - "get_text"   提取 JS 渲染后的可见文字（默认）
            - "get_title"  获取页面 <title>
            - "get_html"   获取 <body> 外层 HTML（限 5000 字）
            - "screenshot" 截图保存至 /tmp，返回文件路径
            - "click"      点击 CSS 选择器匹配的元素（需 selector）
            - "fill"       向输入框填入文字（需 selector、value）
            - "scroll"     向下滚动页面（可选 distance，默认 800px）
        selector: CSS 选择器，用于 click / fill 操作
        value: 填入的文本内容，用于 fill 操作
        distance: 滚动距离（像素），用于 scroll 操作，默认 800
        wait_ms: 页面加载后额外等待毫秒数（等 JS 执行），默认 1500

    Returns:
        操作结果文本或文件路径
    """
    if USE_PLAYWRIGHT:
        return _browser_use_playwright(url, action, selector, value, distance, wait_ms)
    else:
        return _browser_use_httpx(url, action)


def _browser_use_playwright(
    url: str,
    action: str,
    selector: str,
    value: str,
    distance: int,
    wait_ms: int,
) -> ToolResponse:
    """Playwright 实现的 browser_use。

    sync_playwright() 内部会调用 asyncio.run()，若当前线程已有运行中的 event loop
    （FastAPI/uvicorn 场景）则直接冲突。解决方案：将 Playwright 操作提交到一个
    独立 worker 线程（该线程没有 event loop），主线程同步等待结果。
    """
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            _playwright_task, url, action, selector, value, distance, wait_ms
        )
        try:
            result = future.result(timeout=35)  # 比 goto timeout 多留余量
        except concurrent.futures.TimeoutError:
            return _text_response("<error>Playwright 操作超时（35s）</error>")
        except Exception as e:
            return _text_response(f"<error>Playwright 操作失败: {e}</error>")

    return _text_response(result)


def _playwright_task(
    url: str,
    action: str,
    selector: str,
    value: str,
    distance: int,
    wait_ms: int,
) -> str:
    """在 worker 线程中执行的 Playwright 操作，返回结果字符串或抛出异常。"""
    import time

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=20000)
        page.wait_for_timeout(wait_ms)

        if action == "get_text":
            text = page.inner_text("body")
            if len(text) > 5000:
                text = text[:5000] + "\n\n[...内容已截断...]"
            result = f"# {url}\n\n{text}"

        elif action == "get_title":
            result = f"页面标题: {page.title()}"

        elif action == "get_html":
            html = page.inner_html("body")
            if len(html) > 5000:
                html = html[:5000] + "\n\n[...内容已截断...]"
            result = f"# {url} HTML\n\n{html}"

        elif action == "screenshot":
            ts = int(time.time())
            path = f"/tmp/agentpal_screenshot_{ts}.png"
            page.screenshot(path=path)
            result = f"截图已保存: {path}"

        elif action == "click":
            if not selector:
                browser.close()
                raise ValueError("click 操作需要提供 selector 参数")
            page.click(selector)
            result = f"✅ 已点击元素: {selector}"

        elif action == "fill":
            if not selector:
                browser.close()
                raise ValueError("fill 操作需要提供 selector 参数")
            page.fill(selector, value)
            result = f"✅ 已填入内容到 {selector}"

        elif action == "scroll":
            page.evaluate(f"window.scrollBy(0, {distance})")
            result = f"✅ 已向下滚动 {distance}px"

        else:
            browser.close()
            raise ValueError(
                f"不支持的 action: {action}，"
                "可用值: get_text / get_title / get_html / screenshot / click / fill / scroll"
            )

        browser.close()
        return result


def _browser_use_httpx(url: str, action: str) -> ToolResponse:
    """httpx 降级实现（不支持 JS 渲染，仅 get_text / get_title）。"""
    try:
        import re

        import httpx

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AgentPal/0.1"
        }
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()

        if action == "get_title":
            match = re.search(r"<title>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
            title = match.group(1).strip() if match else "（无标题）"
            return _text_response(f"页面标题: {title}")

        # get_text（及其他不支持的 action 统一降级为 get_text）
        text = re.sub(r"<script[^>]*>.*?</script>", "", resp.text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s{3,}", "\n\n", text).strip()
        if len(text) > 3000:
            text = text[:3000] + "\n\n[...内容已截断...]"
        return _text_response(
            f"[注意：Playwright 未安装，使用 httpx 降级模式，不支持 JS 渲染]\n# {url}\n\n{text}"
        )

    except Exception as e:
        return _text_response(f"<error>访问失败: {e}</error>")


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
                    })
                    await db.commit()
                    return (
                        f"✅ 定时任务创建成功！\n"
                        f"  名称: {result['name']}\n"
                        f"  ID: {result['id']}\n"
                        f"  计划: {result['schedule']}\n"
                        f"  下次执行: {result.get('next_run_at', '计算中...')}\n"
                        f"  提示词: {result['task_prompt'][:100]}"
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
                if not update_data:
                    return "<error>update 操作至少需要提供一个要更新的字段（name/schedule/task_prompt）</error>"

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


# ── 10. execute_python_code ──────────────────────────────


def execute_python_code(
    code: str,
    packages: list[str] | None = None,
    timeout: int = 30,
) -> ToolResponse:
    """在独立子进程中执行 Python 代码，支持动态安装依赖包。

    代码在 Agent 工作空间目录下运行，stdout / stderr 完整捕获并返回。
    每次调用启动全新 Python 进程，与主进程完全隔离。

    Args:
        code: 要执行的 Python 代码（支持多行、import、print 等）
        packages: 执行前需要 pip install 的包名列表，如 ["pandas", "matplotlib"]
        timeout: 代码执行的最长等待秒数（默认 30 秒）

    Returns:
        包含 returncode、stdout、stderr 的执行结果
    """
    import sys
    import tempfile

    settings = get_settings()
    workspace = Path(settings.workspace_dir).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)

    # ── 1. 可选：安装依赖包 ──────────────────────────────
    if packages:
        install_result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", *packages],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if install_result.returncode != 0:
            return _text_response(
                f"<error>pip install 失败\n{install_result.stderr.strip()}</error>"
            )

    # ── 2. 写入临时文件并执行 ────────────────────────────
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            encoding="utf-8",
            dir=workspace,
            prefix="_agentpal_exec_",
        ) as tmp:
            tmp.write(code)
            tmp_path = tmp.name

        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=workspace,
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        # 截断超长输出，防止撑爆上下文
        max_chars = 8000
        if len(stdout) > max_chars:
            stdout = stdout[:max_chars] + f"\n\n[... stdout 已截断，共 {len(result.stdout)} 字符]"
        if len(stderr) > max_chars:
            stderr = stderr[:max_chars] + f"\n\n[... stderr 已截断，共 {len(result.stderr)} 字符]"

        output = (
            f"<returncode>{result.returncode}</returncode>\n"
            f"<stdout>{stdout}</stdout>\n"
            f"<stderr>{stderr}</stderr>"
        )
        return _text_response(output)

    except subprocess.TimeoutExpired:
        return _text_response(f"<error>Python 代码执行超时（{timeout} 秒）</error>")
    except Exception as e:
        return _text_response(f"<error>{e}</error>")
    finally:
        # 清理临时文件
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass


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
        "name": "execute_python_code",
        "func": execute_python_code,
        "description": "在独立子进程中动态执行 Python 代码，支持安装依赖包",
        "icon": "Code2",
        "dangerous": True,
    },
]
