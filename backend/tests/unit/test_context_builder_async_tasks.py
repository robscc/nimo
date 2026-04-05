"""ContextBuilder 异步任务结果注入 — 单元测试。"""

from __future__ import annotations

from agentpal.workspace.context_builder import ContextBuilder, WorkspaceFiles


class TestContextBuilderAsyncTasks:
    """测试 ContextBuilder 对 async_task_results 的注入。"""

    def test_build_without_async_tasks(self):
        """无异步任务时不产生 Async Task Results section。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="test")
        prompt = cb.build_system_prompt(ws)
        assert "Async Task Results" not in prompt

    def test_build_with_empty_async_tasks(self):
        """空列表时不产生 section。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="test")
        prompt = cb.build_system_prompt(ws, async_task_results=[])
        assert "Async Task Results" not in prompt

    def test_build_with_sub_agent_result(self):
        """SubAgent 完成结果正确注入。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="test")
        results = [
            {
                "source": "sub_agent",
                "task_id": "task-123",
                "execution_id": None,
                "agent_name": "coder",
                "task_prompt": "修复登录 Bug",
                "status": "done",
                "result": "已修复 auth.py 中的 token 校验逻辑",
                "error": "",
                "finished_at": "2026-03-27T10:00:00+00:00",
            },
        ]
        prompt = cb.build_system_prompt(ws, async_task_results=results)
        assert "Async Task Results" in prompt
        assert "coder" in prompt
        assert "修复登录 Bug" in prompt
        assert "sub_agent" in prompt
        assert "task-123" in prompt
        assert "已修复 auth.py 中的 token 校验逻辑" in prompt

    def test_build_with_cron_result(self):
        """Cron 完成结果正确注入。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="test")
        results = [
            {
                "source": "cron",
                "task_id": None,
                "execution_id": "exec-xyz",
                "agent_name": "cron",
                "task_prompt": "定时任务「每日报告」",
                "status": "done",
                "result": "系统正常运行",
                "error": "",
                "finished_at": "2026-03-27T09:00:00+00:00",
            },
        ]
        prompt = cb.build_system_prompt(ws, async_task_results=results)
        assert "Async Task Results" in prompt
        assert "每日报告" in prompt
        assert "exec-xyz" in prompt
        assert "系统正常运行" in prompt

    def test_build_with_failed_task(self):
        """失败任务展示错误信息。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="test")
        results = [
            {
                "source": "sub_agent",
                "task_id": "task-fail",
                "execution_id": None,
                "agent_name": "researcher",
                "task_prompt": "搜索资料",
                "status": "failed",
                "result": "",
                "error": "NetworkError: 无法连接到目标服务器",
                "finished_at": "2026-03-27T10:00:00+00:00",
            },
        ]
        prompt = cb.build_system_prompt(ws, async_task_results=results)
        assert "❌" in prompt
        assert "NetworkError" in prompt

    def test_result_truncation(self):
        """超过 max_chars 的结果被截断。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="test")
        long_result = "x" * 1000
        results = [
            {
                "source": "sub_agent",
                "task_id": "task-long",
                "execution_id": None,
                "agent_name": "coder",
                "task_prompt": "长任务",
                "status": "done",
                "result": long_result,
                "error": "",
                "finished_at": "2026-03-27T10:00:00+00:00",
            },
        ]
        prompt = cb.build_system_prompt(ws, async_task_results=results)
        # 默认 max_chars=500，结果应被截断
        assert "已截断" in prompt
        assert long_result not in prompt

    def test_result_truncation_respects_runtime_config(self):
        """runtime_context 中的 async_result_max_chars 覆盖默认值。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="test")
        results = [
            {
                "source": "sub_agent",
                "task_id": "task-1",
                "execution_id": None,
                "agent_name": "a",
                "task_prompt": "任务",
                "status": "done",
                "result": "x" * 200,
                "error": "",
                "finished_at": "2026-03-27T10:00:00+00:00",
            },
        ]
        # 设置小的 max_chars
        runtime_context = {"async_result_max_chars": 50}
        prompt = cb.build_system_prompt(
            ws, async_task_results=results, runtime_context=runtime_context
        )
        assert "已截断" in prompt

    def test_expired_tasks_beyond_max_inject(self):
        """超出 max_inject 数量的任务标记为已过期。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="test")
        results = []
        for i in range(8):
            results.append({
                "source": "sub_agent",
                "task_id": f"task-{i}",
                "execution_id": None,
                "agent_name": f"agent-{i}",
                "task_prompt": f"任务 {i}",
                "status": "done",
                "result": f"结果 {i}",
                "error": "",
                "finished_at": f"2026-03-27T{10-i:02d}:00:00+00:00",
            })
        prompt = cb.build_system_prompt(ws, async_task_results=results)
        # 前 5 个有完整结果，后 3 个标记过期
        assert "已过期" in prompt
        # 验证前 5 个有 Task ID
        assert "task-0" in prompt
        assert "task-4" in prompt

    def test_expired_tasks_max_inject_from_runtime_context(self):
        """runtime_context 中的 async_result_max_inject 覆盖默认值。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="test")
        results = []
        for i in range(5):
            results.append({
                "source": "sub_agent",
                "task_id": f"task-{i}",
                "execution_id": None,
                "agent_name": f"agent-{i}",
                "task_prompt": f"任务 {i}",
                "status": "done",
                "result": f"结果 {i}",
                "error": "",
                "finished_at": f"2026-03-27T{10-i:02d}:00:00+00:00",
            })
        runtime_context = {"async_result_max_inject": 2}
        prompt = cb.build_system_prompt(
            ws, async_task_results=results, runtime_context=runtime_context
        )
        assert "已过期" in prompt

    def test_mixed_sub_agent_and_cron_sorted(self):
        """SubAgent 和 Cron 混合结果按完成时间排序。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="test")
        results = [
            {
                "source": "cron",
                "task_id": None,
                "execution_id": "exec-1",
                "agent_name": "cron",
                "task_prompt": "定时任务「检查」",
                "status": "done",
                "result": "cron 结果",
                "error": "",
                "finished_at": "2026-03-27T10:00:00+00:00",
            },
            {
                "source": "sub_agent",
                "task_id": "task-1",
                "execution_id": None,
                "agent_name": "researcher",
                "task_prompt": "搜索资料",
                "status": "done",
                "result": "sub 结果",
                "error": "",
                "finished_at": "2026-03-27T09:00:00+00:00",
            },
        ]
        prompt = cb.build_system_prompt(ws, async_task_results=results)
        assert "cron" in prompt
        assert "sub_agent" in prompt
        # 两者都出现
        assert "cron 结果" in prompt
        assert "sub 结果" in prompt

    def test_section_position_between_tools_and_skills(self):
        """async task results 段落应在 skills 之前。"""
        cb = ContextBuilder()
        ws = WorkspaceFiles(identity="test")
        results = [
            {
                "source": "sub_agent",
                "task_id": "task-1",
                "execution_id": None,
                "agent_name": "coder",
                "task_prompt": "任务",
                "status": "done",
                "result": "结果",
                "error": "",
                "finished_at": "2026-03-27T10:00:00+00:00",
            },
        ]
        skill_prompts = [{"name": "test-skill", "content": "skill content"}]
        prompt = cb.build_system_prompt(
            ws, async_task_results=results, skill_prompts=skill_prompts
        )
        async_pos = prompt.index("Async Task Results")
        skill_pos = prompt.index("Installed Skills")
        assert async_pos < skill_pos
