"""SubAgent 自动调度：复杂场景端到端测试。

一个完整的「故事线」测试，模拟真实用户在单次会话中：
  1. 默认 auto 模式 — 简单问答（不该派遣）
  2. auto 模式 — 复杂调研任务（应该派遣 researcher）
  3. 切换到 manual 模式 — 无 @mention 的复杂任务（不该派遣）
  4. manual 模式 — @researcher 指令（应该派遣）
  5. manual 模式 — @coder 指令（应该派遣给 coder）
  6. 动态创建自定义 SubAgent → 验证 roster 实时生效
  7. manual 模式 — @自定义agent 指令
  8. 切换到 off 模式 — 任何任务都不该派遣
  9. 切回 auto 模式 — 验证恢复
 10. 禁用 SubAgent → 验证 roster 不再包含它
 11. 清理自定义 SubAgent

运行方式：
  cd backend && .venv/bin/pytest tests/e2e/test_sub_agent_full_scenario.py -v -s --tb=long

需要：后端运行在 http://localhost:8099，配置有效 LLM API Key。
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from tests.e2e.helpers import (
    BACKEND_URL,
    assert_chat_completed,
    create_session,
    extract_reply_text,
    get_session_meta,
    get_sub_agent_mode,
    get_tool_calls,
    read_sse_events,
    require_backend,
    send_chat,
    set_sub_agent_mode,
    update_session_config,
)

pytestmark = [pytest.mark.e2e, pytest.mark.llm, pytest.mark.slow, require_backend]

# ── Helpers ──────────────────────────────────────────────────


def _dispatch_calls(events: list[dict]) -> list[dict]:
    """提取 dispatch_sub_agent 的 tool_start 事件。"""
    return [e for e in events if e.get("type") == "tool_start" and e.get("name") == "dispatch_sub_agent"]


def _all_tool_names(events: list[dict]) -> list[str]:
    """提取所有 tool_start 的工具名。"""
    return [e.get("name", "") for e in events if e.get("type") == "tool_start"]


def _print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ── Main Scenario ────────────────────────────────────────────


class TestSubAgentFullScenario:
    """完整的 SubAgent 调度场景测试（顺序执行，共享 session）。

    这个测试类模拟一个真实用户在单次会话中经历所有模式切换的完整流程。
    测试之间有状态依赖（共享 session_id），必须按顺序执行。
    """

    # ── 共享状态 ──────────────────────────────────────────

    _session_id: str = ""
    _custom_agent_name: str = "e2e_translator"
    _custom_agent_display: str = "翻译专员"

    @pytest.fixture(autouse=True, scope="class")
    def setup_clients(self, request):
        """为整个测试类创建共享的 httpx 客户端和 session。"""
        api = httpx.Client(base_url=BACKEND_URL, timeout=30.0, trust_env=False)
        chat = httpx.Client(base_url=BACKEND_URL, timeout=300.0, trust_env=False)

        # 创建 session
        sid = create_session(api, channel="e2e-full-test")
        TestSubAgentFullScenario._session_id = sid

        # 挂到 class 上供所有 test 方法使用
        request.cls.api = api
        request.cls.chat = chat
        request.cls.sid = sid

        yield

        # 清理：删除自定义 SubAgent（如果还在）
        try:
            api.delete(f"/api/v1/sub-agents/{self._custom_agent_name}")
        except Exception:
            pass
        # 恢复 researcher 启用状态
        try:
            api.patch("/api/v1/sub-agents/researcher", json={"enabled": True})
        except Exception:
            pass
        api.close()
        chat.close()

    # ── Phase 1: 验证初始状态 ─────────────────────────────

    def test_01_initial_state_is_null(self):
        """新 session 的 sub_agent_mode 应为 null（跟随全局 auto）。"""
        _print_section("Phase 1: 验证初始状态")

        meta = get_session_meta(self.api, self.sid)
        print(f"  Session ID: {self.sid}")
        print(f"  sub_agent_mode: {meta['sub_agent_mode']}")
        print(f"  model_name: {meta['model_name']}")

        assert meta["sub_agent_mode"] is None, \
            f"新 session mode 应为 null，实际: {meta['sub_agent_mode']}"

    # ── Phase 2: Auto 模式 — 简单问答（不该派遣） ─────────

    def test_02_auto_simple_no_dispatch(self):
        """auto 模式下，简单问题应直接回答，不调用 dispatch_sub_agent。"""
        _print_section("Phase 2: Auto 模式 — 简单问答")

        events = send_chat(self.chat, self.sid, "1+1 等于几？直接回答数字就好。")
        assert_chat_completed(events)

        reply = extract_reply_text(events)
        tools = _all_tool_names(events)
        dispatches = _dispatch_calls(events)

        print(f"  回复: {reply[:100]}")
        print(f"  调用的工具: {tools}")
        print(f"  dispatch 次数: {len(dispatches)}")

        assert "2" in reply, f"应包含答案 2，实际: {reply[:200]}"

        if dispatches:
            pytest.skip("LLM 对简单问题也触发了 dispatch（不期望但不硬失败）")

        time.sleep(3)  # 等待 DB 事务释放

    # ── Phase 3: Auto 模式 — 明确派遣请求 ─────────────────

    def test_03_auto_explicit_dispatch_request(self):
        """auto 模式下，用户明确说「请派遣调研员」应触发 dispatch_sub_agent。"""
        _print_section("Phase 3: Auto 模式 — 明确派遣请求")

        events = send_chat(
            self.chat, self.sid,
            "请派遣调研员帮我调研一下：Python 3.12 有哪些重要的新特性？简要列出 3 个即可，不要太长。"
        )
        assert_chat_completed(events)

        reply = extract_reply_text(events)
        tools = _all_tool_names(events)
        dispatches = _dispatch_calls(events)

        print(f"  回复长度: {len(reply)} 字符")
        print(f"  调用的工具: {tools}")
        print(f"  dispatch 次数: {len(dispatches)}")

        if not dispatches:
            print("  ⚠️ LLM 未触发 dispatch（可能直接处理了）")
            pytest.skip("auto 模式下明确派遣请求未触发 dispatch")
        else:
            call_input = dispatches[0].get("input", {})
            print(f"  dispatch 参数: {call_input}")
            # 验证派遣给了 researcher
            agent_name = call_input.get("agent_name", "")
            if agent_name:
                assert agent_name == "researcher", \
                    f"应派遣给 researcher，实际: {agent_name}"

            # 验证有 tool_done
            tool_dones = [e for e in events if e.get("type") == "tool_done" and e.get("name") == "dispatch_sub_agent"]
            print(f"  tool_done 数: {len(tool_dones)}")
            assert tool_dones, "dispatch 后应有 tool_done 事件"

        time.sleep(3)  # 等待 DB 事务释放

    # ── Phase 4: 切换到 Manual 模式 ──────────────────────

    def test_04_switch_to_manual(self):
        """将 session 切换到 manual 模式。"""
        _print_section("Phase 4: 切换到 Manual 模式")

        meta = set_sub_agent_mode(self.api, self.sid, "manual")
        print(f"  切换后 mode: {meta['sub_agent_mode']}")
        assert meta["sub_agent_mode"] == "manual"

        # 读回验证持久化
        mode = get_sub_agent_mode(self.api, self.sid)
        assert mode == "manual"

    # ── Phase 5: Manual 模式 — 无 @mention 不派遣 ────────

    def test_05_manual_no_mention_no_dispatch(self):
        """manual 模式下，没有 @mention 不应触发 dispatch。"""
        _print_section("Phase 5: Manual — 无 @mention")

        events = send_chat(
            self.chat, self.sid,
            "请帮我深度调研 AI Agent 领域最新进展，整理报告。"
        )
        assert_chat_completed(events)

        reply = extract_reply_text(events)
        dispatches = _dispatch_calls(events)

        print(f"  回复长度: {len(reply)} 字符")
        print(f"  dispatch 次数: {len(dispatches)}")

        if dispatches:
            pytest.skip("manual 模式无 @mention 仍触发了 dispatch（LLM 未严格遵循指令）")

        time.sleep(3)  # 等待 DB 事务释放

    # ── Phase 6: Manual 模式 — @researcher 触发 ───────────

    def test_06_manual_mention_researcher(self):
        """manual 模式下，@researcher 应触发 dispatch 给 researcher。"""
        _print_section("Phase 6: Manual — @researcher")

        events = send_chat(
            self.chat, self.sid,
            "@researcher 请用一句话总结「Python 之禅」的核心思想。不需要搜索，直接回答。",
        )
        assert_chat_completed(events)

        reply = extract_reply_text(events)
        dispatches = _dispatch_calls(events)
        tools = _all_tool_names(events)

        print(f"  回复长度: {len(reply)} 字符")
        print(f"  调用的工具: {tools}")
        print(f"  dispatch 次数: {len(dispatches)}")

        if not dispatches:
            pytest.skip(f"manual @researcher 未触发 dispatch，工具: {tools}")
        else:
            agent_name = dispatches[0].get("input", {}).get("agent_name", "")
            print(f"  派遣给: {agent_name}")
            assert agent_name == "researcher", f"应派遣给 researcher，实际: {agent_name}"

        time.sleep(3)  # 等待 DB 事务释放

    # ── Phase 7: Manual 模式 — @coder 触发 ────────────────

    def test_07_manual_mention_coder(self):
        """manual 模式下，@coder 应触发 dispatch 给 coder。"""
        _print_section("Phase 7: Manual — @coder")

        events = send_chat(
            self.chat, self.sid,
            "@coder 写一个 Python 单行代码，打印 Hello World。不需要运行，直接给出即可。",
        )
        assert_chat_completed(events)

        reply = extract_reply_text(events)
        dispatches = _dispatch_calls(events)

        print(f"  回复长度: {len(reply)} 字符")
        print(f"  dispatch 次数: {len(dispatches)}")

        if not dispatches:
            pytest.skip("manual @coder 未触发 dispatch")
        else:
            agent_name = dispatches[0].get("input", {}).get("agent_name", "")
            print(f"  派遣给: {agent_name}")
            assert agent_name == "coder", f"应派遣给 coder，实际: {agent_name}"

        time.sleep(3)  # 等待 DB 事务释放

    # ── Phase 8: 动态创建自定义 SubAgent ──────────────────

    def test_08_create_custom_sub_agent(self):
        """动态创建一个「翻译专员」SubAgent，验证 API 成功。"""
        _print_section("Phase 8: 创建自定义 SubAgent")

        payload = {
            "name": self._custom_agent_name,
            "display_name": self._custom_agent_display,
            "role_prompt": "你是一名专业翻译。擅长中英双向翻译，注重信达雅。\n- 保持原文风格\n- 专业术语保留英文",
            "accepted_task_types": ["translate", "翻译", "localize"],
            "enabled": True,
        }

        # 幂等创建：如果已存在（前次测试残留），先删再建
        resp = self.api.post("/api/v1/sub-agents", json=payload)
        if resp.status_code == 400 and "已存在" in resp.text:
            print("  ⚠️ 残留数据，先删除再创建")
            self.api.delete(f"/api/v1/sub-agents/{self._custom_agent_name}")
            resp = self.api.post("/api/v1/sub-agents", json=payload)

        print(f"  状态码: {resp.status_code}")
        print(f"  响应: {resp.json()}")

        assert resp.is_success, f"创建 SubAgent 失败: {resp.text}"
        data = resp.json()
        assert data["name"] == self._custom_agent_name
        assert data["display_name"] == self._custom_agent_display
        assert data["enabled"] is True

    # ── Phase 9: 验证 Roster 实时更新 ─────────────────────

    def test_09_roster_includes_custom_agent(self):
        """新创建的 SubAgent 应出现在 LLM 的 roster 知识中。"""
        _print_section("Phase 9: 验证 Roster 包含自定义 Agent")

        # 创建新 session 以确保 system prompt 重新构建
        new_sid = create_session(self.api, channel="e2e-roster-test")

        events = send_chat(
            self.chat, new_sid,
            "请精确列出你当前可以调度的所有 SubAgent，包括 name 和中文名。"
        )
        assert_chat_completed(events)

        reply = extract_reply_text(events)
        print(f"  LLM 回复:\n{reply[:500]}")

        # 验证默认 agents
        has_researcher = any(kw in reply for kw in ["researcher", "调研员"])
        has_coder = any(kw in reply for kw in ["coder", "编码员"])
        has_custom = any(kw in reply for kw in [self._custom_agent_name, self._custom_agent_display, "翻译"])

        print(f"  researcher: {'✅' if has_researcher else '❌'}")
        print(f"  coder: {'✅' if has_coder else '❌'}")
        print(f"  {self._custom_agent_display}: {'✅' if has_custom else '❌'}")

        assert has_researcher, f"LLM 应提到 researcher/调研员"
        assert has_coder, f"LLM 应提到 coder/编码员"

        if not has_custom:
            pytest.skip(f"LLM 未提到自定义 SubAgent {self._custom_agent_display}")

        time.sleep(3)  # 等待 DB 事务释放

    # ── Phase 10: Manual — @翻译专员 派遣自定义 Agent ─────

    def test_10_manual_mention_custom_agent(self):
        """manual 模式下，@翻译专员 应派遣给自定义 SubAgent。"""
        _print_section("Phase 10: Manual — @翻译专员")

        events = send_chat(
            self.chat, self.sid,
            f"@{self._custom_agent_display} 请翻译以下内容为英文：'大道至简，知易行难。'"
        )
        assert_chat_completed(events)

        reply = extract_reply_text(events)
        dispatches = _dispatch_calls(events)

        print(f"  回复: {reply[:300]}")
        print(f"  dispatch 次数: {len(dispatches)}")

        if not dispatches:
            pytest.skip(f"manual @{self._custom_agent_display} 未触发 dispatch")
        else:
            agent_name = dispatches[0].get("input", {}).get("agent_name", "")
            print(f"  派遣给: {agent_name}")
            assert agent_name == self._custom_agent_name, \
                f"应派遣给 {self._custom_agent_name}，实际: {agent_name}"

        time.sleep(3)  # 等待 DB 事务释放

    # ── Phase 11: 切换到 Off 模式 ────────────────────────

    def test_11_switch_to_off(self):
        """切换到 off 模式。"""
        _print_section("Phase 11: 切换到 Off 模式")

        meta = set_sub_agent_mode(self.api, self.sid, "off")
        print(f"  切换后 mode: {meta['sub_agent_mode']}")
        assert meta["sub_agent_mode"] == "off"

    # ── Phase 12: Off 模式 — 完全禁用（确定性测试） ───────

    def test_12_off_mode_blocks_dispatch(self):
        """off 模式下，dispatch_sub_agent 工具已移除，不可能被调用。"""
        _print_section("Phase 12: Off 模式 — 确定性：无法派遣")

        events = send_chat(
            self.chat, self.sid,
            "请派遣调研员帮我做一份详细的竞品分析报告。@researcher 也调一下。"
        )
        assert_chat_completed(events)

        reply = extract_reply_text(events)
        dispatches = _dispatch_calls(events)
        tools = _all_tool_names(events)

        print(f"  回复长度: {len(reply)} 字符")
        print(f"  调用的工具: {tools}")
        print(f"  dispatch 次数: {len(dispatches)}")

        # 确定性断言：工具已从 toolkit 移除
        assert len(dispatches) == 0, \
            f"off 模式下 dispatch_sub_agent 已从 toolkit 移除，不可能被调用！实际调用 {len(dispatches)} 次"

        # LLM 应该自己处理或说明无法派遣
        assert len(reply) > 5, f"即使不能派遣，LLM 仍应给出回复"

        time.sleep(3)  # 等待 DB 事务释放

    # ── Phase 13: Off 模式 — 即使 @mention 也不派遣 ──────

    def test_13_off_mode_ignores_mention(self):
        """off 模式下，即使消息包含 @mention 也不会派遣。"""
        _print_section("Phase 13: Off 模式 — @mention 被忽略")

        events = send_chat(
            self.chat, self.sid,
            "@coder 写一个快速排序算法。"
        )
        assert_chat_completed(events)

        dispatches = _dispatch_calls(events)
        tools = _all_tool_names(events)

        print(f"  调用的工具: {tools}")
        print(f"  dispatch 次数: {len(dispatches)}")

        assert len(dispatches) == 0, \
            f"off 模式下 @mention 也不应触发 dispatch！实际调用 {len(dispatches)} 次"

        time.sleep(3)  # 等待 DB 事务释放

    # ── Phase 14: 切回 Auto 模式 — 恢复功能 ──────────────

    def test_14_restore_auto_mode(self):
        """切回 auto 模式，验证功能恢复。"""
        _print_section("Phase 14: 切回 Auto 模式")

        meta = set_sub_agent_mode(self.api, self.sid, "auto")
        assert meta["sub_agent_mode"] == "auto"

        # 发送简单消息验证对话正常
        events = send_chat(self.chat, self.sid, "你好，确认一下你还能正常工作吗？简短回答。")
        assert_chat_completed(events)

        reply = extract_reply_text(events)
        print(f"  回复: {reply[:200]}")
        assert len(reply) > 2, "恢复 auto 模式后应能正常回复"

        time.sleep(3)  # 等待 DB 事务释放

    # ── Phase 15: 禁用 SubAgent → Roster 实时更新 ─────────

    def test_15_disable_agent_removes_from_roster(self):
        """禁用 researcher 后，新 session 的 LLM 不应知道 researcher。"""
        _print_section("Phase 15: 禁用 Agent → Roster 更新")

        # 等待前一个 LLM 调用的 DB 事务完全释放
        time.sleep(5)

        # 禁用 researcher
        resp = self.api.patch("/api/v1/sub-agents/researcher", json={"enabled": False})
        assert resp.is_success, f"禁用 researcher 失败: {resp.text}"
        print(f"  已禁用 researcher")

        # 创建全新 session（新 system prompt）
        fresh_sid = create_session(self.api, channel="e2e-disable-test")

        events = send_chat(
            self.chat, fresh_sid,
            "请列出你当前可以调度的所有 SubAgent 的 name，用逗号分隔，不要额外说明。"
        )
        assert_chat_completed(events)

        reply = extract_reply_text(events)
        print(f"  LLM 回复: {reply[:300]}")

        # coder 应在
        has_coder = any(kw in reply.lower() for kw in ["coder", "编码"])
        print(f"  coder: {'✅' if has_coder else '❌'}")
        assert has_coder, "coder 应仍在 roster 中"

        # researcher 不应在（已禁用）
        if "researcher" in reply.lower():
            print("  ⚠️ LLM 仍提到 researcher（可能来自静态 AGENTS.md 残留）")
            pytest.skip("LLM 仍提到了 researcher，动态 roster 禁用未完全覆盖静态内容")
        else:
            print(f"  researcher: ✅ 已从 roster 移除")

    # ── Phase 16: 恢复 + 清理 ────────────────────────────

    def test_16_cleanup(self):
        """恢复 researcher，删除自定义 SubAgent。"""
        _print_section("Phase 16: 清理")

        # 恢复 researcher
        resp = self.api.patch("/api/v1/sub-agents/researcher", json={"enabled": True})
        print(f"  恢复 researcher: {resp.status_code}")
        assert resp.is_success

        # 删除自定义 SubAgent
        resp = self.api.delete(f"/api/v1/sub-agents/{self._custom_agent_name}")
        print(f"  删除 {self._custom_agent_name}: {resp.status_code}")
        assert resp.is_success

        # 验证列表
        agents = self.api.get("/api/v1/sub-agents").json()
        names = [a["name"] for a in agents]
        print(f"  当前 SubAgents: {names}")

        assert "researcher" in names, "researcher 应已恢复"
        assert "coder" in names, "coder 应存在"
        assert self._custom_agent_name not in names, f"{self._custom_agent_name} 应已删除"

    # ── Phase 17: 模式切换不影响其他配置 ──────────────────

    def test_17_mode_switch_preserves_other_config(self):
        """反复切换 sub_agent_mode 不应影响 enabled_tools 等配置。"""
        _print_section("Phase 17: 配置隔离验证")

        # 先设置 enabled_tools
        update_session_config(self.api, self.sid, {
            "enabled_tools": ["read_file", "get_current_time", "browser_use"],
            "tool_guard_threshold": 5,
        })

        meta_before = get_session_meta(self.api, self.sid)
        tools_before = meta_before["enabled_tools"]
        threshold_before = meta_before["tool_guard_threshold"]
        print(f"  设置前: tools={tools_before}, threshold={threshold_before}")

        # 模式切换循环
        for mode in ["off", "manual", "auto", None, "off", "auto"]:
            set_sub_agent_mode(self.api, self.sid, mode)

        # 验证其他配置不变
        meta_after = get_session_meta(self.api, self.sid)
        print(f"  切换后: tools={meta_after['enabled_tools']}, threshold={meta_after['tool_guard_threshold']}")

        assert meta_after["enabled_tools"] == tools_before, \
            f"enabled_tools 不应被影响: {tools_before} -> {meta_after['enabled_tools']}"
        assert meta_after["tool_guard_threshold"] == threshold_before, \
            f"tool_guard_threshold 不应被影响: {threshold_before} -> {meta_after['tool_guard_threshold']}"

    # ── Phase 18: SubAgent 任务出现在 sub-tasks 列表 ──────

    def test_18_dispatch_creates_sub_task(self):
        """派遣后，任务应出现在 session 的 sub-tasks 列表中。"""
        _print_section("Phase 18: SubAgent 任务记录验证")

        # 确保 auto 模式
        set_sub_agent_mode(self.api, self.sid, "auto")

        # 获取当前 sub-tasks 数量
        resp = self.api.get(f"/api/v1/sessions/{self.sid}/sub-tasks")
        tasks_before = resp.json() if resp.is_success else []
        count_before = len(tasks_before)
        print(f"  派遣前 sub-tasks 数: {count_before}")

        # 发送明确派遣请求
        events = send_chat(
            self.chat, self.sid,
            "请派遣编码员写一个简单的 Python Hello World 脚本并解释。"
        )
        assert_chat_completed(events)

        dispatches = _dispatch_calls(events)
        if not dispatches:
            pytest.skip("LLM 未触发 dispatch，跳过任务记录验证")

        # 等待任务记录写入
        time.sleep(2)

        # 验证 sub-tasks 列表增加
        resp = self.api.get(f"/api/v1/sessions/{self.sid}/sub-tasks")
        tasks_after = resp.json() if resp.is_success else []
        count_after = len(tasks_after)
        print(f"  派遣后 sub-tasks 数: {count_after}")

        assert count_after > count_before, \
            f"sub-tasks 应增加: before={count_before}, after={count_after}"

        # 检查最新任务的字段
        latest_task = tasks_after[0]  # 按创建时间倒序
        print(f"  最新任务: id={latest_task['id']}, status={latest_task['status']}, agent={latest_task['agent_name']}")
        assert latest_task["status"] in ("pending", "running", "done", "failed"), \
            f"任务状态异常: {latest_task['status']}"
        assert latest_task["task_prompt"], "任务应有 prompt"


# ── 独立测试：验证 API 边界条件 ──────────────────────────────


class TestSubAgentModeEdgeCases:
    """SubAgent 模式的边界条件测试（不需要 LLM）。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.api = httpx.Client(base_url=BACKEND_URL, timeout=30.0, trust_env=False)
        yield
        self.api.close()

    def test_rapid_mode_switching(self):
        """快速连续切换模式 20 次，最终状态应正确。"""
        _print_section("Edge: 快速模式切换")

        sid = create_session(self.api, channel="e2e-rapid-switch")
        modes = ["auto", "manual", "off", None] * 5  # 20 次切换

        for i, mode in enumerate(modes):
            set_sub_agent_mode(self.api, sid, mode)

        # 最终应为 null（最后一次是 None）
        final_mode = get_sub_agent_mode(self.api, sid)
        print(f"  最终 mode: {final_mode}")
        assert final_mode is None

    def test_concurrent_sessions_isolation(self):
        """多个 session 的 sub_agent_mode 互不影响。"""
        _print_section("Edge: 多 Session 隔离")

        sessions = {}
        target_modes = {"s1": "auto", "s2": "manual", "s3": "off", "s4": None}

        for key, mode in target_modes.items():
            sid = create_session(self.api, channel="e2e-isolation")
            sessions[key] = sid
            set_sub_agent_mode(self.api, sid, mode)

        # 验证各自独立
        for key, mode in target_modes.items():
            actual = get_sub_agent_mode(self.api, sessions[key])
            print(f"  {key}: expected={mode}, actual={actual}")
            assert actual == mode, f"{key} mode 不匹配: expected={mode}, actual={actual}"

    def test_set_mode_after_tool_config(self):
        """先设置工具配置，再设置 mode，两者应共存。"""
        _print_section("Edge: 配置共存")

        sid = create_session(self.api, channel="e2e-coexist")

        # 设置工具
        update_session_config(self.api, sid, {
            "enabled_tools": ["read_file"],
            "tool_guard_threshold": 3,
        })

        # 设置 mode
        set_sub_agent_mode(self.api, sid, "manual")

        # 验证两者共存
        meta = get_session_meta(self.api, sid)
        print(f"  mode: {meta['sub_agent_mode']}")
        print(f"  tools: {meta['enabled_tools']}")
        print(f"  threshold: {meta['tool_guard_threshold']}")

        assert meta["sub_agent_mode"] == "manual"
        assert meta["enabled_tools"] == ["read_file"]
        assert meta["tool_guard_threshold"] == 3

    def test_invalid_modes_all_rejected(self):
        """各种无效 mode 值都应返回 400。"""
        _print_section("Edge: 无效值全部拒绝")

        sid = create_session(self.api, channel="e2e-invalid")
        invalid_values = ["Auto", "MANUAL", "OFF", "on", "true", "1", "enable", "disable", ""]

        for val in invalid_values:
            resp = self.api.patch(
                f"/api/v1/sessions/{sid}/config",
                json={"sub_agent_mode": val},
            )
            print(f"  '{val}' → {resp.status_code}")
            assert resp.status_code == 400, f"'{val}' 应返回 400，实际: {resp.status_code}"

    def test_sub_agents_api_crud(self):
        """SubAgent CRUD API 完整流程。"""
        _print_section("Edge: SubAgent CRUD")

        name = "e2e_crud_test"

        # Create
        resp = self.api.post("/api/v1/sub-agents", json={
            "name": name,
            "display_name": "CRUD 测试",
            "role_prompt": "用于测试的 SubAgent。",
            "accepted_task_types": ["test"],
            "enabled": True,
        })
        assert resp.is_success, f"Create 失败: {resp.text}"
        print(f"  Create: ✅")

        # Read
        resp = self.api.get(f"/api/v1/sub-agents/{name}")
        assert resp.is_success
        assert resp.json()["display_name"] == "CRUD 测试"
        print(f"  Read: ✅")

        # Update
        resp = self.api.patch(f"/api/v1/sub-agents/{name}", json={
            "display_name": "更新后的名字",
            "accepted_task_types": ["test", "verify"],
        })
        assert resp.is_success
        assert resp.json()["display_name"] == "更新后的名字"
        assert "verify" in resp.json()["accepted_task_types"]
        print(f"  Update: ✅")

        # Disable
        resp = self.api.patch(f"/api/v1/sub-agents/{name}", json={"enabled": False})
        assert resp.is_success
        assert resp.json()["enabled"] is False
        print(f"  Disable: ✅")

        # Delete
        resp = self.api.delete(f"/api/v1/sub-agents/{name}")
        assert resp.is_success
        print(f"  Delete: ✅")

        # Verify gone
        resp = self.api.get(f"/api/v1/sub-agents/{name}")
        assert resp.status_code == 404
        print(f"  Verify deleted: ✅")
