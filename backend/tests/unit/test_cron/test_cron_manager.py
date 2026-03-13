"""CronManager 单元测试。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agentpal.database import Base
from agentpal.models.cron import CronJob, CronJobExecution, CronStatus
from agentpal.services.cron_scheduler import CronManager, validate_cron_expression, _compute_next_run


TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


class TestCronExpressionValidation:
    def test_valid_expression(self):
        assert validate_cron_expression("0 9 * * *") is True
        assert validate_cron_expression("*/5 * * * *") is True
        assert validate_cron_expression("0 0 1 * *") is True

    def test_invalid_expression(self):
        assert validate_cron_expression("invalid") is False
        assert validate_cron_expression("") is False

    def test_compute_next_run(self):
        result = _compute_next_run("0 9 * * *")
        assert result is not None
        assert result > datetime.now(timezone.utc)


class TestCronManager:
    @pytest.mark.asyncio
    async def test_create_job(self, db: AsyncSession):
        """创建定时任务。"""
        mgr = CronManager(db)
        job = await mgr.create_job({
            "name": "每日报告",
            "schedule": "0 9 * * *",
            "task_prompt": "生成今日工作报告",
        })
        assert job["name"] == "每日报告"
        assert job["schedule"] == "0 9 * * *"
        assert job["enabled"] is True
        assert job["next_run_at"] is not None

    @pytest.mark.asyncio
    async def test_create_job_invalid_schedule(self, db: AsyncSession):
        """创建无效 cron 表达式应报错。"""
        mgr = CronManager(db)
        with pytest.raises(ValueError, match="无效的 cron 表达式"):
            await mgr.create_job({
                "name": "bad",
                "schedule": "not-a-cron",
                "task_prompt": "test",
            })

    @pytest.mark.asyncio
    async def test_list_jobs(self, db: AsyncSession):
        """列出定时任务。"""
        mgr = CronManager(db)
        await mgr.create_job({"name": "job1", "schedule": "0 9 * * *", "task_prompt": "t1"})
        await mgr.create_job({"name": "job2", "schedule": "0 18 * * *", "task_prompt": "t2"})

        jobs = await mgr.list_jobs()
        assert len(jobs) == 2

    @pytest.mark.asyncio
    async def test_update_job(self, db: AsyncSession):
        """更新定时任务。"""
        mgr = CronManager(db)
        job = await mgr.create_job({
            "name": "原名", "schedule": "0 9 * * *", "task_prompt": "test",
        })

        updated = await mgr.update_job(job["id"], {"name": "新名", "enabled": False})
        assert updated is not None
        assert updated["name"] == "新名"
        assert updated["enabled"] is False

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, db: AsyncSession):
        mgr = CronManager(db)
        result = await mgr.update_job("nonexistent", {"name": "x"})
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_job(self, db: AsyncSession):
        """删除定时任务。"""
        mgr = CronManager(db)
        job = await mgr.create_job({
            "name": "deletable", "schedule": "0 9 * * *", "task_prompt": "test",
        })
        assert await mgr.delete_job(job["id"]) is True
        assert await mgr.get_job(job["id"]) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, db: AsyncSession):
        mgr = CronManager(db)
        assert await mgr.delete_job("nonexistent") is False

    @pytest.mark.asyncio
    async def test_toggle_job(self, db: AsyncSession):
        """启用/禁用定时任务。"""
        mgr = CronManager(db)
        job = await mgr.create_job({
            "name": "toggle", "schedule": "0 9 * * *", "task_prompt": "test",
        })

        # 禁用
        result = await mgr.toggle_job(job["id"], False)
        assert result is not None
        assert result["enabled"] is False

        # 启用
        result = await mgr.toggle_job(job["id"], True)
        assert result is not None
        assert result["enabled"] is True

    @pytest.mark.asyncio
    async def test_get_due_jobs(self, db: AsyncSession):
        """获取到期任务。"""
        mgr = CronManager(db)
        job = await mgr.create_job({
            "name": "due", "schedule": "* * * * *", "task_prompt": "test",
        })

        # 手动设置 next_run_at 为过去的时间
        job_record = await db.get(CronJob, job["id"])
        assert job_record is not None
        job_record.next_run_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        await db.flush()

        due = await mgr.get_due_jobs()
        assert len(due) >= 1

    @pytest.mark.asyncio
    async def test_execution_lifecycle(self, db: AsyncSession):
        """执行记录的创建和完成。"""
        mgr = CronManager(db)
        job = await mgr.create_job({
            "name": "exec-test", "schedule": "0 9 * * *", "task_prompt": "test",
        })

        # 创建执行记录
        execution = await mgr.create_execution(job["id"], "exec-test", "coder")
        assert execution.status == CronStatus.RUNNING

        # 完成执行
        await mgr.finish_execution(
            execution,
            status=CronStatus.DONE,
            result="任务完成",
            execution_log=[{"type": "test", "data": "log"}],
        )
        assert execution.status == CronStatus.DONE
        assert execution.result == "任务完成"
        assert execution.finished_at is not None

    @pytest.mark.asyncio
    async def test_list_executions(self, db: AsyncSession):
        """列出执行记录。"""
        mgr = CronManager(db)
        job = await mgr.create_job({
            "name": "list-exec", "schedule": "0 9 * * *", "task_prompt": "test",
        })

        exec1 = await mgr.create_execution(job["id"], "list-exec")
        exec2 = await mgr.create_execution(job["id"], "list-exec")

        records = await mgr.list_executions(cron_job_id=job["id"])
        assert len(records) == 2

    @pytest.mark.asyncio
    async def test_get_execution_detail(self, db: AsyncSession):
        """获取执行详情（含完整日志）。"""
        mgr = CronManager(db)
        job = await mgr.create_job({
            "name": "detail", "schedule": "0 9 * * *", "task_prompt": "test",
        })

        execution = await mgr.create_execution(job["id"], "detail")
        log_data = [
            {"type": "system_prompt", "content": "..."},
            {"type": "llm_response", "content": "..."},
        ]
        await mgr.finish_execution(execution, result="ok", execution_log=log_data)

        detail = await mgr.get_execution(execution.id)
        assert detail is not None
        assert detail["execution_log"] == log_data
