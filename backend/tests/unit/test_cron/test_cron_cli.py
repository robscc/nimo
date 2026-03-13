"""cron_cli 内置工具函数测试。

使用 mock 替代真实 DB 来测试工具函数的参数验证和分支逻辑。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpal.tools.builtin import cron_cli


class TestCronCliActions:
    """cron_cli 工具各操作分支测试。"""

    def _extract_text(self, response) -> str:
        """从 ToolResponse 提取文本。"""
        return response.content[0]["text"]

    @patch("agentpal.tools.builtin.cron_cli.__module__", "agentpal.tools.builtin")
    def test_list_empty(self):
        """list 操作 — 无任务时应返回提示。"""
        with patch("agentpal.database.AsyncSessionLocal") as mock_session_cls:
            mock_db = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("agentpal.services.cron_scheduler.CronManager") as MockMgr:
                mock_mgr = AsyncMock()
                mock_mgr.list_jobs.return_value = []
                MockMgr.return_value = mock_mgr

                result = cron_cli(action="list")
                text = self._extract_text(result)
                assert "当前没有定时任务" in text

    def test_list_with_jobs(self):
        """list 操作 — 有任务时应显示列表。"""
        with patch("agentpal.database.AsyncSessionLocal") as mock_session_cls:
            mock_db = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("agentpal.services.cron_scheduler.CronManager") as MockMgr:
                mock_mgr = AsyncMock()
                mock_mgr.list_jobs.return_value = [
                    {
                        "id": "test-id-1",
                        "name": "每日报告",
                        "schedule": "0 9 * * *",
                        "enabled": True,
                        "next_run_at": "2026-03-14T01:00:00+00:00",
                    },
                    {
                        "id": "test-id-2",
                        "name": "周报",
                        "schedule": "0 9 * * 1",
                        "enabled": False,
                        "next_run_at": None,
                    },
                ]
                MockMgr.return_value = mock_mgr

                result = cron_cli(action="list")
                text = self._extract_text(result)
                assert "2 个" in text
                assert "每日报告" in text
                assert "周报" in text

    def test_create_missing_name(self):
        """create 操作 — 缺少 name 应报错。"""
        result = cron_cli(action="create", schedule="0 9 * * *", task_prompt="test")
        text = self._extract_text(result)
        assert "name" in text
        assert "error" in text

    def test_create_missing_schedule(self):
        """create 操作 — 缺少 schedule 应报错。"""
        result = cron_cli(action="create", name="test", task_prompt="test")
        text = self._extract_text(result)
        assert "schedule" in text
        assert "error" in text

    def test_create_missing_task_prompt(self):
        """create 操作 — 缺少 task_prompt 应报错。"""
        result = cron_cli(action="create", name="test", schedule="0 9 * * *")
        text = self._extract_text(result)
        assert "task_prompt" in text
        assert "error" in text

    def test_create_success(self):
        """create 操作 — 参数齐全应成功创建。"""
        with patch("agentpal.database.AsyncSessionLocal") as mock_session_cls:
            mock_db = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("agentpal.services.cron_scheduler.CronManager") as MockMgr:
                mock_mgr = AsyncMock()
                mock_mgr.create_job.return_value = {
                    "id": "new-id",
                    "name": "每日报告",
                    "schedule": "0 9 * * *",
                    "task_prompt": "生成今日报告",
                    "next_run_at": "2026-03-14T01:00:00+00:00",
                }
                MockMgr.return_value = mock_mgr

                result = cron_cli(
                    action="create",
                    name="每日报告",
                    schedule="0 9 * * *",
                    task_prompt="生成今日报告",
                )
                text = self._extract_text(result)
                assert "创建成功" in text
                assert "每日报告" in text

    def test_update_missing_job_id(self):
        """update 操作 — 缺少 job_id 应报错。"""
        result = cron_cli(action="update", name="new name")
        text = self._extract_text(result)
        assert "job_id" in text
        assert "error" in text

    def test_update_no_fields(self):
        """update 操作 — 无更新字段应报错。"""
        result = cron_cli(action="update", job_id="some-id")
        text = self._extract_text(result)
        assert "至少需要" in text

    def test_delete_missing_job_id(self):
        """delete 操作 — 缺少 job_id 应报错。"""
        result = cron_cli(action="delete")
        text = self._extract_text(result)
        assert "job_id" in text

    def test_toggle_missing_job_id(self):
        """toggle 操作 — 缺少 job_id 应报错。"""
        result = cron_cli(action="toggle")
        text = self._extract_text(result)
        assert "job_id" in text

    def test_unknown_action(self):
        """未知操作应报错。"""
        result = cron_cli(action="unknown_action")
        text = self._extract_text(result)
        assert "不支持的操作" in text
        assert "unknown_action" in text

    def test_history_empty(self):
        """history 操作 — 无记录应返回提示。"""
        with patch("agentpal.database.AsyncSessionLocal") as mock_session_cls:
            mock_db = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("agentpal.services.cron_scheduler.CronManager") as MockMgr:
                mock_mgr = AsyncMock()
                mock_mgr.list_executions.return_value = []
                MockMgr.return_value = mock_mgr

                result = cron_cli(action="history")
                text = self._extract_text(result)
                assert "暂无执行记录" in text

    def test_history_with_records(self):
        """history 操作 — 有记录时应显示列表。"""
        with patch("agentpal.database.AsyncSessionLocal") as mock_session_cls:
            mock_db = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("agentpal.services.cron_scheduler.CronManager") as MockMgr:
                mock_mgr = AsyncMock()
                mock_mgr.list_executions.return_value = [
                    {
                        "id": "exec-1",
                        "cron_job_name": "每日报告",
                        "status": "done",
                        "started_at": "2026-03-13T09:00:00",
                        "result": "报告已生成",
                        "error": None,
                    },
                ]
                MockMgr.return_value = mock_mgr

                result = cron_cli(action="history")
                text = self._extract_text(result)
                assert "每日报告" in text
                assert "报告已生成" in text

    def test_delete_success(self):
        """delete 操作 — 成功删除。"""
        with patch("agentpal.database.AsyncSessionLocal") as mock_session_cls:
            mock_db = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("agentpal.services.cron_scheduler.CronManager") as MockMgr:
                mock_mgr = AsyncMock()
                mock_mgr.delete_job.return_value = True
                MockMgr.return_value = mock_mgr

                result = cron_cli(action="delete", job_id="test-id")
                text = self._extract_text(result)
                assert "已删除" in text

    def test_toggle_success(self):
        """toggle 操作 — 成功切换。"""
        with patch("agentpal.database.AsyncSessionLocal") as mock_session_cls:
            mock_db = AsyncMock()
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("agentpal.services.cron_scheduler.CronManager") as MockMgr:
                mock_mgr = AsyncMock()
                mock_mgr.toggle_job.return_value = {
                    "name": "每日报告",
                    "enabled": False,
                }
                MockMgr.return_value = mock_mgr

                result = cron_cli(action="toggle", job_id="test-id", enabled=False)
                text = self._extract_text(result)
                assert "禁用" in text
                assert "每日报告" in text
