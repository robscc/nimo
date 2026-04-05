"""PA system prompt 异步任务结果注入 — 集成测试。

使用内存 SQLite 数据库，验证 PA._load_async_task_results() 能正确查询
SubAgentTask 和 CronJobExecution 表，并将结果注入到 system prompt 中。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentpal.database import Base

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db_session():
    """创建内存数据库并返回 AsyncSession。"""
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    await engine.dispose()


class TestPAAsyncTaskInject:
    """验证 PersonalAssistant._load_async_task_results() 的集成行为。"""

    @pytest.mark.asyncio
    async def test_load_sub_agent_tasks(self, db_session: AsyncSession):
        """能从 SubAgentTask 表加载已完成的子任务结果。"""
        from agentpal.models.session import SubAgentTask, TaskStatus

        session_id = "test-session-1"

        # 插入一条已完成的 SubAgent 任务
        task = SubAgentTask(
            id=str(uuid.uuid4()),
            parent_session_id=session_id,
            sub_session_id=f"sub:{session_id}:t1",
            task_prompt="分析代码质量",
            status=TaskStatus.DONE,
            agent_name="coder",
            result="代码质量良好，覆盖率 85%",
            execution_log=[],
            meta={},
            finished_at=datetime.now(timezone.utc),
        )
        db_session.add(task)
        await db_session.commit()

        # 构建 PA 并加载结果
        from agentpal.agents.personal_assistant import PersonalAssistant
        from agentpal.memory.factory import MemoryFactory

        memory = MemoryFactory.create("buffer")
        pa = PersonalAssistant(
            session_id=session_id,
            memory=memory,
            db=db_session,
        )

        results = await pa._load_async_task_results()
        assert len(results) >= 1

        sub_results = [r for r in results if r["source"] == "sub_agent"]
        assert len(sub_results) == 1
        assert sub_results[0]["agent_name"] == "coder"
        assert "代码质量良好" in sub_results[0]["result"]
        assert sub_results[0]["status"] in ("done", "done")

    @pytest.mark.asyncio
    async def test_load_cron_executions(self, db_session: AsyncSession):
        """能从 CronJobExecution + CronJob 表加载已完成的定时任务结果。"""
        from agentpal.models.cron import CronJob, CronJobExecution, CronStatus

        session_id = "test-session-2"

        # 插入 CronJob（关联到 session）
        job = CronJob(
            id=str(uuid.uuid4()),
            name="每日报告",
            schedule="0 9 * * *",
            task_prompt="生成每日报告",
            agent_name="reporter",
            target_session_id=session_id,
        )
        db_session.add(job)
        await db_session.flush()

        # 插入执行记录
        execution = CronJobExecution(
            id=str(uuid.uuid4()),
            cron_job_id=job.id,
            cron_job_name=job.name,
            status=CronStatus.DONE,
            agent_name="reporter",
            result="系统运行正常，CPU 使用率 23%",
            finished_at=datetime.now(timezone.utc),
        )
        db_session.add(execution)
        await db_session.commit()

        # 构建 PA 并加载结果
        from agentpal.agents.personal_assistant import PersonalAssistant
        from agentpal.memory.factory import MemoryFactory

        memory = MemoryFactory.create("buffer")
        pa = PersonalAssistant(
            session_id=session_id,
            memory=memory,
            db=db_session,
        )

        results = await pa._load_async_task_results()
        cron_results = [r for r in results if r["source"] == "cron"]
        assert len(cron_results) == 1
        assert "每日报告" in cron_results[0]["task_prompt"]
        assert "系统运行正常" in cron_results[0]["result"]

    @pytest.mark.asyncio
    async def test_mixed_results_sorted_by_finished_at(self, db_session: AsyncSession):
        """SubAgent 和 Cron 混合结果按完成时间倒序排列。"""
        from agentpal.models.cron import CronJob, CronJobExecution, CronStatus
        from agentpal.models.session import SubAgentTask, TaskStatus

        session_id = "test-session-3"

        # SubAgent 完成于 T+2
        task = SubAgentTask(
            id=str(uuid.uuid4()),
            parent_session_id=session_id,
            sub_session_id=f"sub:{session_id}:t1",
            task_prompt="SubAgent 任务",
            status=TaskStatus.DONE,
            agent_name="coder",
            result="sub 结果",
            execution_log=[],
            meta={},
            finished_at=datetime(2026, 3, 27, 12, 0, 0, tzinfo=timezone.utc),
        )
        db_session.add(task)

        # Cron 完成于 T+1（更早）
        job = CronJob(
            id=str(uuid.uuid4()),
            name="检查任务",
            schedule="*/5 * * * *",
            task_prompt="检查",
            target_session_id=session_id,
        )
        db_session.add(job)
        await db_session.flush()

        execution = CronJobExecution(
            id=str(uuid.uuid4()),
            cron_job_id=job.id,
            cron_job_name=job.name,
            status=CronStatus.DONE,
            agent_name="cron",
            result="cron 结果",
            finished_at=datetime(2026, 3, 27, 10, 0, 0, tzinfo=timezone.utc),
        )
        db_session.add(execution)
        await db_session.commit()

        from agentpal.agents.personal_assistant import PersonalAssistant
        from agentpal.memory.factory import MemoryFactory

        memory = MemoryFactory.create("buffer")
        pa = PersonalAssistant(session_id=session_id, memory=memory, db=db_session)

        results = await pa._load_async_task_results()
        assert len(results) == 2
        # SubAgent（T+2）应在前面，Cron（T+1）在后面
        assert results[0]["source"] == "sub_agent"
        assert results[1]["source"] == "cron"

    @pytest.mark.asyncio
    async def test_no_tasks_returns_empty(self, db_session: AsyncSession):
        """没有异步任务时返回空列表。"""
        from agentpal.agents.personal_assistant import PersonalAssistant
        from agentpal.memory.factory import MemoryFactory

        memory = MemoryFactory.create("buffer")
        pa = PersonalAssistant(
            session_id="empty-session",
            memory=memory,
            db=db_session,
        )

        results = await pa._load_async_task_results()
        assert results == []

    @pytest.mark.asyncio
    async def test_only_completed_tasks_returned(self, db_session: AsyncSession):
        """只返回已完成（done/failed）的任务，不返回 running/pending。"""
        from agentpal.models.session import SubAgentTask, TaskStatus

        session_id = "test-session-4"

        for status in [TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.RUNNING, TaskStatus.PENDING]:
            task = SubAgentTask(
                id=str(uuid.uuid4()),
                parent_session_id=session_id,
                sub_session_id=f"sub:{session_id}:{status.value}",
                task_prompt=f"任务 {status.value}",
                status=status,
                agent_name="test",
                result="结果" if status in (TaskStatus.DONE, TaskStatus.FAILED) else None,
                error="错误" if status == TaskStatus.FAILED else None,
                execution_log=[],
                meta={},
                finished_at=datetime.now(timezone.utc) if status in (TaskStatus.DONE, TaskStatus.FAILED) else None,
            )
            db_session.add(task)
        await db_session.commit()

        from agentpal.agents.personal_assistant import PersonalAssistant
        from agentpal.memory.factory import MemoryFactory

        memory = MemoryFactory.create("buffer")
        pa = PersonalAssistant(session_id=session_id, memory=memory, db=db_session)

        results = await pa._load_async_task_results()
        # 只有 done 和 failed
        statuses = {r["status"] for r in results}
        assert statuses <= {"done", "failed"}
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_no_db_returns_empty(self):
        """db=None 时返回空列表（不崩溃）。"""
        from agentpal.agents.personal_assistant import PersonalAssistant
        from agentpal.memory.factory import MemoryFactory

        memory = MemoryFactory.create("buffer")
        pa = PersonalAssistant(session_id="no-db", memory=memory, db=None)

        results = await pa._load_async_task_results()
        assert results == []
