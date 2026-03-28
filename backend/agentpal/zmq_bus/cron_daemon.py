"""CronDaemon — 定时任务调度守护进程。

基于 AgentDaemon 基类，以单例方式运行。

职责：
- 周期调度：每 30 秒检查到期 CronJob 并异步执行
- 手动触发：处理 CRON_TRIGGER 消息（来自 API）
- 通知路由：任务完成后通过 ZMQ DEALER→ROUTER 通知 PA daemon
- 事件发布：通过 ZMQ PUB 发布 session 级实时事件
- 审计记录：写入 DB（CronJobExecution）保留完整执行日志
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from agentpal.zmq_bus.daemon import AgentDaemon
from agentpal.zmq_bus.protocol import Envelope, MessageType


class CronDaemon(AgentDaemon):
    """定时任务调度守护进程。

    identity 固定为 ``"cron:scheduler"``，全局唯一。

    启动时自动拉起 _scheduler_loop 后台任务，周期检查到期 CronJob。
    同时处理来自 API 的 CRON_TRIGGER 消息以支持手动触发。
    """

    CHECK_INTERVAL = 30  # 秒

    def __init__(self) -> None:
        super().__init__(identity="cron:scheduler")
        self._scheduler_task: asyncio.Task | None = None

    # ── 生命周期 ──────────────────────────────────────────

    async def start(self, ctx, router_addr: str, events_addr: str) -> None:
        """启动 daemon + 调度循环 + heartbeat 初始化。"""
        await super().start(ctx, router_addr, events_addr)

        self._scheduler_task = asyncio.create_task(
            self._scheduler_loop(), name="cron-scheduler-loop"
        )
        # 幂等地确保 heartbeat 定时任务存在
        asyncio.create_task(self._ensure_heartbeat_job())

        logger.info("CronDaemon 调度循环已启动")

    async def stop(self) -> None:
        """停止调度循环 + 基类优雅关闭。"""
        # 先取消调度循环
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None

        await super().stop()
        logger.info("CronDaemon 调度循环已停止")

    # ── 消息处理 ──────────────────────────────────────────

    async def handle_message(self, envelope: Envelope) -> None:
        """处理接收到的消息。

        目前仅处理 CRON_TRIGGER（API 手动触发），
        其余消息类型记录警告后忽略。
        """
        if envelope.msg_type == MessageType.CRON_TRIGGER:
            await self._handle_cron_trigger(envelope)
        else:
            logger.warning(
                f"CronDaemon 收到未知消息类型: {envelope.msg_type}"
            )

    async def _handle_cron_trigger(self, envelope: Envelope) -> None:
        """处理手动触发请求。

        payload 需包含 ``job_id``，daemon 从 DB 读取完整 CronJob 信息后执行。
        """
        job_id = envelope.payload.get("job_id")
        if not job_id:
            logger.warning("CRON_TRIGGER 缺少 job_id，忽略")
            return

        from agentpal.database import AsyncSessionLocal
        from agentpal.services.cron_scheduler import CronManager

        async with AsyncSessionLocal() as db:
            mgr = CronManager(db)
            job_dict = await mgr.get_job(job_id)
            if not job_dict:
                logger.warning(f"CRON_TRIGGER: 任务不存在 job_id={job_id}")
                return

            logger.info(f"手动触发定时任务: {job_dict['name']} (ID={job_id})")
            asyncio.create_task(
                self._execute_job(
                    job_id=job_dict["id"],
                    job_name=job_dict["name"],
                    task_prompt=job_dict["task_prompt"],
                    agent_name=job_dict["agent_name"],
                    notify_main=job_dict["notify_main"],
                    target_session_id=job_dict.get("target_session_id"),
                )
            )

    # ── 调度循环 ──────────────────────────────────────────

    async def _scheduler_loop(self) -> None:
        """后台循环：每 CHECK_INTERVAL 秒检查到期任务并异步执行。"""
        while self._running:
            try:
                await self._check_and_run()
            except Exception as e:
                logger.error(f"CronDaemon 调度检查异常: {e}")
            await asyncio.sleep(self.CHECK_INTERVAL)

    async def _check_and_run(self) -> None:
        """检查到期任务并 spawn 异步执行。"""
        from agentpal.database import AsyncSessionLocal
        from agentpal.services.cron_scheduler import CronManager

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
                    self._execute_job(
                        job_id=job.id,
                        job_name=job.name,
                        task_prompt=job.task_prompt,
                        agent_name=job.agent_name,
                        notify_main=job.notify_main,
                        target_session_id=job.target_session_id,
                    )
                )

    # ── 任务执行 ──────────────────────────────────────────

    async def _execute_job(
        self,
        job_id: str,
        job_name: str,
        task_prompt: str,
        agent_name: str | None,
        notify_main: bool,
        target_session_id: str | None = None,
    ) -> None:
        """执行单个定时任务。

        流程：
        1. 创建 DB 执行记录（CronJobExecution）
        2. 使用 CronAgent 执行任务，收集完整日志
        3. 写入执行结果到 DB
        4. 通过 ZMQ 通知 PA daemon（替代原 MessageBus 路径）
        5. 通过 ZMQ PUB 发布 session 级实时事件
        """
        from agentpal.database import AsyncSessionLocal
        from agentpal.models.cron import CronStatus
        from agentpal.services.cron_scheduler import CronManager

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

                # 通知主 Agent（通过 ZMQ 路由）
                if notify_main:
                    await self._notify_via_zmq(
                        job_name=job_name,
                        result=result,
                        agent_name=agent_name,
                        target_session_id=target_session_id,
                        db=db,
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

    # ── ZMQ 通知（替代原 MessageBus + SessionEventBus）──────

    async def _notify_via_zmq(
        self,
        job_name: str,
        result: str,
        agent_name: str | None,
        target_session_id: str | None,
        db: Any,
    ) -> None:
        """通过 ZMQ 将定时任务结果通知到 PA daemon。

        若指定了 target_session_id：
        - 将结果写入该 session 的 MemoryRecord（DB 审计）
        - 通过 DEALER→ROUTER 发送 AGENT_NOTIFY 到对应 PA daemon
        - 通过 PUB 发布 STREAM_EVENT 供 SSE 推送

        否则：
        - 发送 AGENT_NOTIFY 到通用 "pa:__cron__" 目标
        """
        sender = agent_name or "cron"
        content = result[:2000]
        meta = {
            "card_type": "cron_result",
            "job_name": job_name,
            "agent_name": sender,
        }

        if target_session_id:
            # 写入指定 session 的记忆记录（DB 审计）
            from agentpal.models.memory import MemoryRecord

            record = MemoryRecord(
                id=str(uuid.uuid4()),
                session_id=target_session_id,
                role="assistant",
                content=content,
                meta=meta,
            )
            db.add(record)
            await db.flush()

            # 通过 DEALER→ROUTER 发送 AGENT_NOTIFY 到 PA daemon
            notify_envelope = Envelope(
                msg_type=MessageType.AGENT_NOTIFY,
                source=self.identity,
                target=f"pa:{target_session_id}",
                session_id=target_session_id,
                payload={
                    "job_name": job_name,
                    "result": result[:2000],
                    "agent_name": sender,
                    "type": "cron_result",
                },
            )
            await self.send_to_router(notify_envelope)

            # 通过 PUB 发布 STREAM_EVENT 供前端 SSE 实时推送
            created_at = datetime.now(timezone.utc).isoformat()
            event_envelope = Envelope(
                msg_type=MessageType.STREAM_EVENT,
                source=self.identity,
                target="",
                payload={
                    "type": "new_message",
                    "message": {
                        "id": record.id,
                        "role": "assistant",
                        "content": content,
                        "created_at": created_at,
                        "meta": meta,
                    },
                },
            )
            await self.publish_event(
                f"session:{target_session_id}", event_envelope
            )

            logger.info(
                f"定时任务结果已通过 ZMQ 通知 session {target_session_id}"
            )
        else:
            # 无指定 session，发送到通用目标
            notify_envelope = Envelope(
                msg_type=MessageType.AGENT_NOTIFY,
                source=self.identity,
                target="pa:__cron__",
                session_id="__cron__",
                payload={
                    "job_name": job_name,
                    "result": result[:2000],
                    "agent_name": sender,
                    "type": "cron_result",
                },
            )
            await self.send_to_router(notify_envelope)

    # ── 复用 CronScheduler 的核心逻辑 ─────────────────────

    async def _ensure_heartbeat_job(self) -> None:
        """确保 heartbeat 定时任务存在（幂等）。

        读取 config 中的 heartbeat 配置，自动创建或更新内置 heartbeat cron job。
        直接复用 CronScheduler._ensure_heartbeat_job 的逻辑。
        """
        from agentpal.config import get_settings
        from agentpal.database import AsyncSessionLocal
        from agentpal.models.cron import CronJob
        from agentpal.services.cron_scheduler import _compute_next_run

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
                from sqlalchemy import select

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
                        logger.info(
                            f"Heartbeat 定时任务已更新: schedule={cron_expr}"
                        )
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
                    logger.info(
                        f"Heartbeat 定时任务已创建: schedule={cron_expr}"
                    )

        except Exception as e:
            logger.error(f"Heartbeat 初始化失败: {e}")

    async def _run_cron_agent(
        self,
        db: Any,
        task_prompt: str,
        agent_name: str | None,
        execution_log: list[dict[str, Any]],
    ) -> str:
        """用 CronAgent 执行定时任务，收集完整日志。

        复用 CronScheduler._run_cron_agent 的逻辑：
        - 普通 cron 任务：上下文只加载 AGENTS.md + SOUL.md
        - Heartbeat 任务（agent_name == '__heartbeat__'）：加载完整工作空间上下文
        """
        from pathlib import Path

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
                line
                for line in heartbeat_content.strip().splitlines()
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
