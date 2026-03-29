"""SubAgent 派遣 / 代码执行 / 产出物相关工具。"""

from __future__ import annotations

import mimetypes
import subprocess
from pathlib import Path
from typing import Any

from agentscope.tool import ToolResponse

from agentpal.config import get_settings
from agentpal.tools.builtin_fs import _text_response


# ── _get_scheduler ────────────────────────────────────────


def _get_scheduler() -> Any:
    """获取 Scheduler 实例（支持 FastAPI 主进程和 PA 子进程）。

    1. FastAPI 主进程：从 app.state.scheduler 获取 SchedulerClient
    2. PA 子进程：从 worker 模块获取 WorkerSchedulerProxy
    3. 其他场景（测试等）：返回 None
    """
    # 优先检查子进程代理（PA 子进程中 app.state 不存在）
    try:
        from agentpal.scheduler.worker import _worker_scheduler_proxy
        if _worker_scheduler_proxy is not None:
            return _worker_scheduler_proxy
    except Exception:
        pass

    # FastAPI 主进程路径
    try:
        import agentpal.main as _main_mod
        _app = getattr(_main_mod, "app", None)
        if _app is not None:
            return getattr(_app.state, "scheduler", None)
    except Exception:
        pass
    return None


# ── 10. dispatch_sub_agent ───────────────────────────────


async def dispatch_sub_agent(
    task_prompt: str,
    parent_session_id: str,
    task_type: str = "",
    agent_name: str = "",
    wait_seconds: int = 120,
    blocking: bool = False,
    runtime_type: str = "internal",
    runtime_config: dict[str, Any] | None = None,
) -> ToolResponse:
    """将子任务委托给专业 SubAgent 执行，并等待结果返回。

    Args:
        task_prompt: 要委托执行的任务描述（越详细越好）
        parent_session_id: 当前主 Agent 的 session_id（见系统提示 Runtime Environment）
        task_type: 任务类型，用于自动路由到合适的 SubAgent（可选）
                   可选值: code / debug / script / research / summarize / analyze 等
        agent_name: 直接指定 SubAgent 名称（可选），如 "coder"、"researcher"
                    agent_name 优先级高于 task_type
        wait_seconds: 最长等待秒数（默认 120），超时后返回 task_id 供后续查询

    Returns:
        SubAgent 执行结果文本，超时时返回任务 ID 和当前状态
    """
    import asyncio
    import uuid

    from agentpal.agents.personal_assistant import _default_model_config
    from agentpal.agents.registry import SubAgentRegistry
    from agentpal.database import AsyncSessionLocal
    from agentpal.models.agent import SubAgentDefinition
    from agentpal.models.session import SubAgentTask, TaskStatus
    from agentpal.runtimes.base import ExecutionResult, RuntimeConfig
    from agentpal.runtimes.registry import get_runtime

    async def _run() -> str:
        from agentpal.agents.sub_agent import SubAgent
        from agentpal.memory.buffer import BufferMemory

        task_id = str(uuid.uuid4())
        sub_session_id = f"sub:{parent_session_id}:{task_id}"

        async with AsyncSessionLocal() as db:
            # 1. 查找 SubAgent 定义（与 PersonalAssistant.dispatch_sub_agent 逻辑一致）
            registry = SubAgentRegistry(db)
            agent_def: SubAgentDefinition | None = None
            role_prompt = ""
            max_tool_rounds = 8
            resolved_agent_name = agent_name or None
            model_config = _default_model_config()

            if agent_name:
                agent_def = await db.get(SubAgentDefinition, agent_name)
            elif task_type:
                agent_def = await registry.find_agent_for_task(task_type)

            if agent_def:
                resolved_agent_name = agent_def.name
                role_prompt = agent_def.role_prompt or ""
                model_config = agent_def.get_model_config(model_config)
                max_tool_rounds = agent_def.max_tool_rounds

            # 2. 创建任务记录
            task = SubAgentTask(
                id=task_id,
                parent_session_id=parent_session_id,
                sub_session_id=sub_session_id,
                task_prompt=task_prompt,
                status=TaskStatus.PENDING,
                agent_name=resolved_agent_name,
                task_type=task_type or None,
                execution_log=[],
                meta={"blocking": blocking, "wait_seconds": wait_seconds},
            )
            db.add(task)
            await db.commit()

            if not blocking:
                # ── Non-blocking: 优先使用 Scheduler 子进程模式 ──
                scheduler = _get_scheduler()
                if scheduler is not None:
                    # Scheduler 可用 → 子进程模式
                    # 结果投递由 Scheduler._deliver_sub_result 统一处理
                    await scheduler.dispatch_sub_agent(
                        task_id=task_id,
                        task_prompt=task_prompt,
                        parent_session_id=parent_session_id,
                        agent_name=resolved_agent_name or "default",
                        model_config=model_config,
                        role_prompt=role_prompt,
                        max_tool_rounds=max_tool_rounds,
                    )
                    return (
                        f"[SubAgent] STARTED (subprocess)\n"
                        f"任务 ID: {task_id}\n"
                        f"执行者：{resolved_agent_name or 'Auto'}\n"
                        f"状态：{TaskStatus.RUNNING.value}\n\n"
                        f"任务正在后台运行，可在 /tasks 页面查看进度。"
                    )
                else:
                    # Fallback: Scheduler 不可用，使用 in-process asyncio.create_task
                    _task_id = task_id
                    _sub_session_id = sub_session_id
                    _model_config = model_config
                    _role_prompt = role_prompt
                    _max_tool_rounds = max_tool_rounds
                    _parent_session_id = parent_session_id
                    _resolved_agent_name = resolved_agent_name

                    async def run_in_background() -> None:
                        """Fallback: 在进程内独立 AsyncSession 中运行 SubAgent。"""
                        import os

                        from loguru import logger as _logger

                        from agentpal.database import AsyncSessionLocal as _ASL

                        # 设置环境变量，供 produce_artifact 工具获取当前任务 ID
                        os.environ["AGENTPAL_CURRENT_TASK_ID"] = _task_id

                        async with _ASL() as bg_db:
                            bg_task = await bg_db.get(SubAgentTask, _task_id)
                            if bg_task is None:
                                _logger.error(
                                    f"SubAgent 后台任务找不到 task record: {_task_id}"
                                )
                                return

                            sub_memory = BufferMemory()
                            sub_agent = SubAgent(
                                session_id=_sub_session_id,
                                memory=sub_memory,
                                task=bg_task,
                                db=bg_db,
                                model_config=_model_config,
                                role_prompt=_role_prompt,
                                max_tool_rounds=_max_tool_rounds,
                                parent_session_id=_parent_session_id,
                            )
                            try:
                                result_text = await sub_agent.run(task_prompt)
                                await bg_db.commit()

                                # ── 将结果写入父 Session 并推送 SSE ──
                                if _parent_session_id and result_text:
                                    import uuid as _uuid

                                    from agentpal.models.memory import (
                                        MemoryRecord,
                                    )
                                    from agentpal.services.session_event_bus import (
                                        session_event_bus,
                                    )

                                    _display = _resolved_agent_name or "SubAgent"
                                    _content = result_text[:4000]
                                    _meta = {
                                        "card_type": "sub_agent_result",
                                        "agent_name": _display,
                                        "task_id": _task_id,
                                    }
                                    _record = MemoryRecord(
                                        id=str(_uuid.uuid4()),
                                        session_id=_parent_session_id,
                                        role="assistant",
                                        content=_content,
                                        meta=_meta,
                                    )
                                    bg_db.add(_record)
                                    await bg_db.flush()
                                    await bg_db.commit()

                                    await session_event_bus.publish(
                                        _parent_session_id,
                                        {
                                            "type": "new_message",
                                            "message": {
                                                "id": _record.id,
                                                "role": "assistant",
                                                "content": _content,
                                                "created_at": (
                                                    _record.created_at.isoformat()
                                                    if _record.created_at
                                                    else None
                                                ),
                                                "meta": _meta,
                                            },
                                        },
                                    )
                                    _logger.info(
                                        f"SubAgent 结果已推送到 session "
                                        f"{_parent_session_id}"
                                    )
                            except Exception as exc:
                                _logger.exception(
                                    f"SubAgent 后台任务执行失败: {exc}"
                                )
                                bg_task.status = TaskStatus.FAILED
                                bg_task.error = str(exc)
                                await bg_db.commit()

                    _task_handle = asyncio.create_task(run_in_background())

                    def _on_bg_done(fut: asyncio.Task) -> None:  # type: ignore[type-arg]
                        from loguru import logger as _bg_logger

                        if fut.cancelled():
                            _bg_logger.warning(f"SubAgent bg task {task_id} was cancelled")
                        elif _exc := fut.exception():
                            _bg_logger.error(f"SubAgent bg task {task_id} unhandled error: {_exc}")

                    _task_handle.add_done_callback(_on_bg_done)

                    return (
                        f"[SubAgent] STARTED\n"
                        f"任务 ID: {task_id}\n"
                        f"执行者：{resolved_agent_name or 'Auto'}\n"
                        f"状态：{TaskStatus.RUNNING.value}\n\n"
                        f"任务正在后台运行，可在 /tasks 页面查看进度。"
                    )
            else:
                # ── Blocking: db 在 async with 内，生命周期安全 ──
                # 让 runtime.execute() 自行管理 _initialize / _cleanup
                rt_config_data = {
                    "runtime_type": runtime_type,
                    "model_config": model_config,
                    "max_tool_rounds": max_tool_rounds,
                    "timeout_seconds": float(wait_seconds),
                }
                if runtime_config:
                    rt_config_data["extra"] = runtime_config

                rt_config = RuntimeConfig(**rt_config_data)

                runtime = get_runtime(
                    runtime_type=runtime_type,
                    session_id=sub_session_id,
                    config=rt_config,
                    db=db,
                    parent_session_id=parent_session_id,
                    task=task,
                )

                result: ExecutionResult = await runtime.execute(task_prompt)

                # 从数据库重新加载最新状态
                await db.refresh(task)

                agent_label = resolved_agent_name or "SubAgent"
                if result.success and task.status == TaskStatus.DONE:
                    return f"[{agent_label}] DONE\n\n{result.output}"
                elif task.status == TaskStatus.INPUT_REQUIRED:
                    question = task.meta.get("input_request", {}).get("question", "需要您的输入")
                    return f"[{agent_label}] INPUT_REQUIRED\n任务 ID: {task_id}\n问题：{question}\n\n请提供所需输入以继续执行。"
                else:
                    error_detail = f"\n\n错误：{task.error}" if task.error else (f"\n\n错误：{result.error}" if result.error else "")
                    return f"[{agent_label}] FAILED\n任务 ID: {task_id}{error_detail}"

    try:
        result_text = await asyncio.wait_for(_run(), timeout=wait_seconds)
        return _text_response(result_text)
    except asyncio.TimeoutError:
        return _text_response(
            f"SubAgent 执行超时（{wait_seconds} 秒），任务仍在后台运行。\n"
            f"可在 /tasks 页面查看进度。"
        )
    except Exception as e:
        return _text_response(f"<error>SubAgent 派遣失败: {e}</error>")


# ── 11. execute_python_code ──────────────────────────────


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


# ── 12. produce_artifact ──────────────────────────────────


def produce_artifact(
    name: str,
    content: str | None = None,
    artifact_type: str = "text",
    file_path: str | None = None,
    mime_type: str | None = None,
    extra: dict[str, Any] | None = None,
) -> ToolResponse:
    """创建任务产出物（代码文件、报告、图表等）。

    SubAgent 使用此工具将执行过程中的中间产物或最终成果保存下来，
    供用户查看和下载。

    Args:
        name: 产出物名称（例如："analysis_report.md"、"generated_code.py"）
        content: 文本内容（text 类型时使用）
        artifact_type: 产出物类型：file/text/image/data（默认"text"）
        file_path: 文件路径（file 类型时使用，可以是绝对路径或相对于工作空间的相对路径）
        mime_type: MIME 类型（例如："text/markdown"、"image/png"、"application/json"）
        extra: 额外元数据（JSON 对象）

    Returns:
        产出物创建结果，包括 artifact_id 和访问路径

    Example:
        # 创建文本报
        produce_artifact(
            name="analysis_report.md",
            content="# Analysis Report\\n\\n...",
            artifact_type="text",
            mime_type="text/markdown"
        )

        # 保存文件
        produce_artifact(
            name="generated_script.py",
            file_path="/path/to/script.py",
            artifact_type="file",
            mime_type="text/x-python"
        )
    """
    import uuid

    from agentpal.database import get_sync_db
    from agentpal.models.session import TaskArtifact

    try:
        # 获取当前任务 ID（从系统环境变量或线程上下文）
        import os

        task_id = os.environ.get("AGENTPAL_CURRENT_TASK_ID")
        if not task_id:
            return _text_response("<error>无法获取当前任务 ID，请在 dispatch_sub_agent 回调中使用此工具</error>")

        # 确定 artifact_type 和 mime_type
        if artifact_type == "text" and not mime_type:
            mime_type = "text/plain"
        elif artifact_type == "file" and not mime_type and file_path:
            mime_type, _ = mimetypes.guess_type(file_path)

        # 计算文件大小
        size_bytes = None
        if content:
            size_bytes = len(content.encode("utf-8"))
        elif file_path:
            p = Path(file_path).expanduser()
            if p.exists():
                size_bytes = p.stat().st_size

        # 保存到数据库
        artifact_id = str(uuid.uuid4())
        with get_sync_db() as db:
            artifact = TaskArtifact(
                id=artifact_id,
                task_id=task_id,
                name=name,
                artifact_type=artifact_type,
                content=content,
                file_path=file_path,
                mime_type=mime_type,
                size_bytes=size_bytes,
                extra=extra or {},
            )
            db.add(artifact)
            db.commit()

        # 尝试发射事件（如果在异步环境中运行）
        try:
            import asyncio

            from agentpal.services.task_event_bus import task_event_bus

            loop = asyncio.get_running_loop()
            loop.create_task(
                task_event_bus.emit(
                    task_id,
                    "task.artifact_created",
                    {"artifact_id": artifact_id, "name": name, "type": artifact_type},
                    f"已创建产出物：{name}",
                )
            )
        except RuntimeError:
            # 没有运行的事件循环，跳过事件发射
            pass

        return _text_response(
            f"[产出物已创建]\n名称：{name}\nID: {artifact_id}\n类型：{artifact_type}\n大小：{size_bytes or 0} 字节"
        )

    except Exception as e:
        return _text_response(f"<error>产出物创建失败：{e}</error>")
