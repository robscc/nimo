"""CronScheduler — 定时任务调度器。

使用 asyncio 后台任务 + croniter 计算执行时间。
在 FastAPI lifespan 中启动，每 30 秒检查一次是否有到期任务。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select, update


def _ts(dt: datetime | None) -> str | None:
    """将 datetime 转为带 UTC 时区标记的 ISO 8601 字符串。

    SQLite + SQLAlchemy 有时会返回 naive datetime（tzinfo=None），
    直接 isoformat() 后缺少 +00:00 后缀，导致 JS 将其解析为本地时间（偏移 8 小时）。
    此函数确保始终输出带 +00:00 的 UTC 字符串。
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
from sqlalchemy.ext.asyncio import AsyncSession

from agentpal.models.cron import CronJob, CronJobExecution, CronStatus


def _compute_next_run(schedule: str, after: datetime | None = None) -> datetime | None:
    """根据 cron 表达式计算下次执行时间。

    使用 croniter 库（可选依赖），不可用时返回 None。
    """
    try:
        from croniter import croniter
    except ImportError:
        logger.warning("croniter 未安装，无法计算 cron 下次执行时间")
        return None

    base = after or datetime.now(timezone.utc)
    # croniter 对 timezone-aware datetime 的处理不稳定：内部会转换为本地时间，
    # 但 get_next(datetime) 返回 naive datetime，导致 .replace(tzinfo=utc) 结果偏移。
    # 解决方案：先剥掉 tzinfo，以 naive UTC 传入，确保输入输出都在 UTC 时间轴上。
    base_naive = base.replace(tzinfo=None)
    try:
        cron = croniter(schedule, base_naive)
        return cron.get_next(datetime).replace(tzinfo=timezone.utc)
    except (ValueError, KeyError) as e:
        logger.warning(f"无效的 cron 表达式 '{schedule}': {e}")
        return None


def validate_cron_expression(schedule: str) -> bool:
    """验证 cron 表达式是否合法。"""
    try:
        from croniter import croniter
        croniter(schedule)
        return True
    except ImportError:
        # 没有 croniter 时做简单格式验证
        parts = schedule.strip().split()
        return len(parts) == 5
    except (ValueError, KeyError):
        return False


class CronManager:
    """定时任务 CRUD 管理。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create_job(self, data: dict[str, Any]) -> dict[str, Any]:
        """创建定时任务。"""
        schedule = data.get("schedule", "")
        if not validate_cron_expression(schedule):
            raise ValueError(f"无效的 cron 表达式: '{schedule}'")

        job = CronJob(
            id=str(uuid.uuid4()),
            name=data.get("name", "未命名任务"),
            schedule=schedule,
            task_prompt=data.get("task_prompt", ""),
            agent_name=data.get("agent_name"),
            enabled=data.get("enabled", True),
            notify_main=data.get("notify_main", True),
            target_session_id=data.get("target_session_id"),
            next_run_at=_compute_next_run(schedule),
        )
        self._db.add(job)
        await self._db.flush()
        logger.info(f"创建定时任务: {job.name} (ID={job.id}, schedule={schedule})")
        return self._to_dict(job)

    async def update_job(self, job_id: str, data: dict[str, Any]) -> dict[str, Any] | None:
        """更新定时任务。"""
        job = await self._db.get(CronJob, job_id)
        if job is None:
            return None

        if "schedule" in data:
            if not validate_cron_expression(data["schedule"]):
                raise ValueError(f"无效的 cron 表达式: '{data['schedule']}'")
            job.schedule = data["schedule"]
            job.next_run_at = _compute_next_run(data["schedule"])

        for key in ("name", "task_prompt", "agent_name", "enabled", "notify_main", "target_session_id"):
            if key in data:
                setattr(job, key, data[key])

        await self._db.flush()
        logger.info(f"更新定时任务: {job.name} (ID={job_id})")
        return self._to_dict(job)

    async def delete_job(self, job_id: str) -> bool:
        """删除定时任务。"""
        job = await self._db.get(CronJob, job_id)
        if job is None:
            return False
        await self._db.delete(job)
        await self._db.flush()
        logger.info(f"删除定时任务: {job.name} (ID={job_id})")
        return True

    async def list_jobs(self) -> list[dict[str, Any]]:
        """列出所有定时任务。"""
        result = await self._db.execute(
            select(CronJob).order_by(CronJob.created_at.desc())
        )
        return [self._to_dict(r) for r in result.scalars().all()]

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        """获取单个定时任务。"""
        job = await self._db.get(CronJob, job_id)
        return self._to_dict(job) if job else None

    async def toggle_job(self, job_id: str, enabled: bool) -> dict[str, Any] | None:
        """启用/禁用定时任务。"""
        job = await self._db.get(CronJob, job_id)
        if job is None:
            return None
        job.enabled = enabled
        if enabled:
            job.next_run_at = _compute_next_run(job.schedule)
        await self._db.flush()
        return self._to_dict(job)

    async def get_due_jobs(self) -> list[CronJob]:
        """获取到期需要执行的任务。"""
        now = datetime.now(timezone.utc)
        result = await self._db.execute(
            select(CronJob).where(
                CronJob.enabled == True,  # noqa: E712
                CronJob.next_run_at <= now,
            )
        )
        return list(result.scalars().all())

    async def mark_job_executed(self, job: CronJob) -> None:
        """标记任务已执行，更新下次执行时间。"""
        now = datetime.now(timezone.utc)
        job.last_run_at = now
        job.next_run_at = _compute_next_run(job.schedule, after=now)
        await self._db.flush()

    async def create_execution(
        self,
        cron_job_id: str,
        cron_job_name: str,
        agent_name: str | None = None,
    ) -> CronJobExecution:
        """创建执行记录。"""
        execution = CronJobExecution(
            id=str(uuid.uuid4()),
            cron_job_id=cron_job_id,
            cron_job_name=cron_job_name,
            status=CronStatus.RUNNING,
            agent_name=agent_name,
        )
        self._db.add(execution)
        await self._db.flush()
        return execution

    async def finish_execution(
        self,
        execution: CronJobExecution,
        *,
        status: str = CronStatus.DONE,
        result: str | None = None,
        error: str | None = None,
        execution_log: list[dict[str, Any]] | None = None,
    ) -> None:
        """完成执行记录。"""
        execution.status = status
        execution.result = result
        execution.error = error
        execution.finished_at = datetime.now(timezone.utc)
        if execution_log is not None:
            execution.execution_log = execution_log
        await self._db.flush()

    async def list_executions(
        self, cron_job_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """列出执行记录。"""
        stmt = (
            select(CronJobExecution)
            .order_by(CronJobExecution.started_at.desc())
            .limit(limit)
        )
        if cron_job_id:
            stmt = stmt.where(CronJobExecution.cron_job_id == cron_job_id)
        result = await self._db.execute(stmt)
        return [self._exec_to_dict(r) for r in result.scalars().all()]

    async def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        """获取单个执行记录（含完整日志）。"""
        record = await self._db.get(CronJobExecution, execution_id)
        if record is None:
            return None
        d = self._exec_to_dict(record)
        d["execution_log"] = record.execution_log or []
        return d

    @staticmethod
    def _to_dict(job: CronJob) -> dict[str, Any]:
        return {
            "id": job.id,
            "name": job.name,
            "schedule": job.schedule,
            "task_prompt": job.task_prompt,
            "agent_name": job.agent_name,
            "enabled": job.enabled,
            "notify_main": job.notify_main,
            "target_session_id": job.target_session_id,
            "last_run_at": _ts(job.last_run_at),
            "next_run_at": _ts(job.next_run_at),
            "created_at": _ts(job.created_at),
            "updated_at": _ts(job.updated_at),
        }

    @staticmethod
    def _exec_to_dict(rec: CronJobExecution) -> dict[str, Any]:
        return {
            "id": rec.id,
            "cron_job_id": rec.cron_job_id,
            "cron_job_name": rec.cron_job_name,
            "status": rec.status,
            "agent_name": rec.agent_name,
            "started_at": _ts(rec.started_at),
            "finished_at": _ts(rec.finished_at),
            "result": rec.result[:500] + "..." if rec.result and len(rec.result) > 500 else rec.result,
            "error": rec.error,
        }


class CronScheduler:
    """后台定时任务调度器 — 在 FastAPI lifespan 中启动。

    每 CHECK_INTERVAL 秒检查一次到期任务，然后异步执行。
    """

    CHECK_INTERVAL = 30  # 秒

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """启动后台调度循环。"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("CronScheduler 已启动")

        # 启动 heartbeat 机制
        asyncio.create_task(self._ensure_heartbeat_job())

    async def stop(self) -> None:
        """停止后台调度循环。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("CronScheduler 已停止")

    async def _loop(self) -> None:
        """后台循环：检查到期任务并执行。"""
        while self._running:
            try:
                await self._check_and_run()
            except Exception as e:
                logger.error(f"CronScheduler 检查异常: {e}")
            await asyncio.sleep(self.CHECK_INTERVAL)

    async def _check_and_run(self) -> None:
        """检查到期任务并异步执行。"""
        from agentpal.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            mgr = CronManager(db)
            due_jobs = await mgr.get_due_jobs()

            for job in due_jobs:
                logger.info(f"定时任务到期: {job.name} (ID={job.id})")
                # 标记已执行，更新 next_run_at
                await mgr.mark_job_executed(job)
                await db.commit()

                # 异步执行任务（不阻塞调度循环）
                asyncio.create_task(
                    self._execute_job(job.id, job.name, job.task_prompt, job.agent_name, job.notify_main, job.target_session_id)
                )

    async def _execute_job(
        self,
        job_id: str,
        job_name: str,
        task_prompt: str,
        agent_name: str | None,
        notify_main: bool,
        target_session_id: str | None = None,
    ) -> None:
        """执行单个定时任务。"""
        from agentpal.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            mgr = CronManager(db)
            execution = await mgr.create_execution(job_id, job_name, agent_name)
            await db.commit()

            execution_log: list[dict[str, Any]] = []
            try:
                result = await self._run_cron_agent(
                    db, task_prompt, agent_name, execution_log
                )
                await mgr.finish_execution(
                    execution,
                    status=CronStatus.DONE,
                    result=result,
                    execution_log=execution_log,
                )
                await db.commit()

                # 通知主 Agent
                if notify_main:
                    await self._notify_main_agent(
                        db, job_name, result, agent_name, target_session_id
                    )
                    await db.commit()

                logger.info(f"定时任务完成: {job_name} (ID={job_id})")

            except Exception as e:
                logger.error(f"定时任务失败: {job_name} (ID={job_id}): {e}")
                await mgr.finish_execution(
                    execution,
                    status=CronStatus.FAILED,
                    error=str(e),
                    execution_log=execution_log,
                )
                await db.commit()

    async def _ensure_heartbeat_job(self) -> None:
        """确保 heartbeat 定时任务存在（幂等）。

        读取 config 中的 heartbeat 配置，自动创建或更新内置 heartbeat cron job。
        heartbeat 任务的 agent_name 标记为 '__heartbeat__' 以便特殊处理。
        """
        from agentpal.config import get_settings
        from agentpal.database import AsyncSessionLocal

        try:
            settings = get_settings()
            if not settings.heartbeat_enabled:
                logger.info("Heartbeat 机制已禁用")
                return

            interval = settings.heartbeat_interval_minutes
            # 构建 cron 表达式：每 N 分钟执行一次
            if interval <= 0:
                interval = 60
            if interval < 60:
                cron_expr = f"*/{interval} * * * *"
            else:
                hours = interval // 60
                cron_expr = f"0 */{hours} * * *" if hours > 1 else "7 * * * *"

            async with AsyncSessionLocal() as db:
                # 检查是否已存在 heartbeat job
                result = await db.execute(
                    select(CronJob).where(CronJob.agent_name == "__heartbeat__")
                )
                existing = result.scalar_one_or_none()

                if existing:
                    # 更新 schedule（如果有变化）
                    if existing.schedule != cron_expr:
                        existing.schedule = cron_expr
                        existing.next_run_at = _compute_next_run(cron_expr)
                        await db.commit()
                        logger.info(f"Heartbeat 定时任务已更新: schedule={cron_expr}")
                else:
                    job = CronJob(
                        id=str(uuid.uuid4()),
                        name="🫀 Heartbeat",
                        schedule=cron_expr,
                        task_prompt="执行 HEARTBEAT.md 中定义的定期检查任务。",
                        agent_name="__heartbeat__",
                        enabled=True,
                        notify_main=True,
                        next_run_at=_compute_next_run(cron_expr),
                    )
                    db.add(job)
                    await db.commit()
                    logger.info(f"Heartbeat 定时任务已创建: schedule={cron_expr}")

        except Exception as e:
            logger.error(f"Heartbeat 初始化失败: {e}")

    async def _run_cron_agent(
        self,
        db: AsyncSession,
        task_prompt: str,
        agent_name: str | None,
        execution_log: list[dict[str, Any]],
    ) -> str:
        """用 SubAgent 执行定时任务，收集完整日志。

        Cron 任务的上下文只加载 AGENTS.md + SOUL.md（不影响主上下文）。
        Heartbeat 任务（agent_name == '__heartbeat__'）加载完整工作空间上下文。
        """
        from agentpal.agents.cron_agent import CronAgent
        from agentpal.agents.registry import SubAgentRegistry
        from agentpal.config import get_settings
        from agentpal.memory.factory import MemoryFactory
        from agentpal.workspace.manager import WorkspaceManager

        settings = get_settings()
        is_heartbeat = agent_name == "__heartbeat__"

        # Heartbeat 任务：先读取 HEARTBEAT.md，如果没有活跃任务则跳过
        if is_heartbeat:
            ws_manager = WorkspaceManager(Path(settings.workspace_dir))
            heartbeat_content = await ws_manager.read_file("HEARTBEAT.md")
            active_lines = [
                line for line in heartbeat_content.strip().splitlines()
                if line.strip()
                and not line.strip().startswith("#")
                and not line.strip().startswith(">")
            ]
            if not active_lines:
                return "Heartbeat 跳过：HEARTBEAT.md 中没有活跃任务。"

            # 用实际的 heartbeat 任务内容替换 task_prompt
            task_prompt = (
                "请执行以下 heartbeat 定期检查任务：\n\n"
                + "\n".join(active_lines)
                + "\n\n请逐项执行，完成后汇总结果。"
            )

        # 获取模型配置
        model_config = {
            "provider": settings.llm_provider,
            "model_name": settings.llm_model,
            "api_key": settings.llm_api_key,
            "base_url": settings.llm_base_url,
        }

        # 如果指定了 SubAgent（非 heartbeat），使用其模型配置
        if agent_name and not is_heartbeat:
            registry = SubAgentRegistry(db)
            agent_def = await registry.get_agent(agent_name)
            if agent_def and agent_def.get("has_custom_model"):
                from agentpal.models.agent import SubAgentDefinition

                defn = await db.get(SubAgentDefinition, agent_name)
                if defn:
                    model_config = defn.get_model_config(model_config)

        # 创建独立记忆（不持久化，任务完成即释放）
        memory = MemoryFactory.create("buffer")

        session_id = f"cron:{uuid.uuid4().hex[:8]}"
        agent = CronAgent(
            session_id=session_id,
            memory=memory,
            model_config=model_config,
            execution_log=execution_log,
            db=db,
            full_context=is_heartbeat,
        )

        return await agent.run(task_prompt)

    async def _notify_main_agent(
        self,
        db: AsyncSession,
        job_name: str,
        result: str,
        agent_name: str | None,
        target_session_id: str | None = None,
    ) -> None:
        """将定时任务结果通知主 Agent。

        若指定了 target_session_id，将结果以 assistant 消息写入该 session，
        并通过 SessionEventBus 推送实时更新；否则走 MessageBus NOTIFY 路径。
        """
        sender = agent_name or "cron"
        content = (
            f"📋 定时任务「{job_name}」执行完成\n\n"
            f"执行者: {sender}\n"
            f"结果:\n{result[:2000]}"
        )

        if target_session_id:
            # 写入指定 session 的记忆，推送 SSE 实时更新
            import uuid as _uuid

            from agentpal.models.memory import MemoryRecord
            from agentpal.services.session_event_bus import session_event_bus

            record = MemoryRecord(
                id=str(_uuid.uuid4()),
                session_id=target_session_id,
                role="assistant",
                content=content,
            )
            db.add(record)
            await db.flush()

            await session_event_bus.publish(
                target_session_id,
                {
                    "type": "new_message",
                    "message": {
                        "id": record.id,
                        "role": "assistant",
                        "content": content,
                        "created_at": _ts(record.created_at),
                    },
                },
            )
            logger.info(f"定时任务结果已写入 session {target_session_id}")
        else:
            from agentpal.agents.message_bus import MessageBus
            from agentpal.models.message import MessageType

            bus = MessageBus(db)
            await bus.send(
                from_agent=sender,
                to_agent="main",
                parent_session_id="__cron__",
                content=content,
                message_type=MessageType.NOTIFY,
                metadata={"cron_job_name": job_name},
            )


# 全局单例
cron_scheduler = CronScheduler()
