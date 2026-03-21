# Plan Mode 设计：Session 内状态机方案

> 设计时间：2026-03-20
> 状态：草案，待确认

## 核心理念

```
所有交互都走 POST /api/v1/agent/chat
Plan 不是独立模块，而是 Agent 的一种「运行状态」
状态持久化在 SessionRecord 上，重启后可恢复
```

---

## 一、状态机定义

```
                    用户发来复杂任务
                    Agent 自主判断 or 用户说"先做个计划"
                          │
                          ▼
    ┌──────────┐   generate   ┌──────────────┐
    │  NORMAL  │ ──────────→  │  PLANNING    │  Agent 正在生成/修改计划
    │  正常对话 │              │  生成计划中   │
    └──────────┘              └──────┬───────┘
         ▲                          │ 计划生成完毕
         │                          ▼
         │                   ┌──────────────┐
         │  用户说"取消计划"  │  CONFIRMING  │  等待用户审批
         │ ◀──────────────── │  等待确认     │
         │                   └──────┬───────┘
         │                          │ 用户说"开始执行" / "批准"
         │                          ▼
         │                   ┌──────────────┐
         │  全部完成 / 取消   │  EXECUTING   │  逐步执行中
         │ ◀──────────────── │  执行计划中   │
         │                   └──────┬───────┘
         │                          │ 一步完成，auto_proceed=false
         │                          ▼
         │                   ┌──────────────┐
         │  用户说"取消"      │  STEP_CONFIRM│  步骤间确认
         │ ◀──────────────── │  步骤确认中   │──→ 用户说"继续" → 回 EXECUTING
         │                   └──────────────┘──→ 用户说"修改计划" → 回 PLANNING
         │
         │  （任何状态 + 用户说"退出计划" → 回 NORMAL）
         ▼
```

**状态枚举**：

```python
class AgentMode(StrEnum):
    NORMAL         = "normal"          # 普通对话
    PLANNING       = "planning"        # 正在生成/修改计划
    CONFIRMING     = "confirming"      # 计划已生成，等待用户审批
    EXECUTING      = "executing"       # 正在执行计划步骤
    STEP_CONFIRM   = "step_confirm"    # 一个步骤完成，等待用户确认继续
```

---

## 二、数据模型：最小侵入

不新增表，只在 `SessionRecord` 上加字段，用一个 JSON 字段存整个计划：

```python
# models/session.py — SessionRecord 新增字段

class SessionRecord(Base):
    # ... 现有字段 ...

    # ── Plan Mode ───────────────────────────────────────
    agent_mode: Mapped[str] = mapped_column(
        String(32), default="normal", server_default="normal"
    )
    plan_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
```

`plan_data` 的 JSON 结构：

```json
{
  "goal": "帮我调研 3 个竞品并写对比报告",
  "summary": "分三步完成：信息收集 → 数据整理 → 报告撰写",
  "auto_proceed": false,
  "current_step": 0,
  "steps": [
    {
      "id": "s1",
      "title": "搜索竞品信息",
      "description": "使用 browser_use 搜索竞品 A/B/C 的核心功能和定价",
      "strategy": "依次搜索每个竞品，提取关键信息并记录",
      "tools": ["browser_use"],
      "status": "completed",
      "result": "已收集到 3 个竞品的功能和定价信息...",
      "started_at": "2026-03-20T10:00:00Z",
      "completed_at": "2026-03-20T10:02:30Z"
    },
    {
      "id": "s2",
      "title": "整理对比数据",
      "description": "将搜索结果整理为结构化对比表格",
      "strategy": "创建 markdown 表格，对比功能/定价/优劣势",
      "tools": [],
      "status": "running",
      "result": null
    },
    {
      "id": "s3",
      "title": "撰写分析报告",
      "description": "基于对比数据撰写 500 字分析报告",
      "strategy": "使用 write_file 输出最终报告",
      "tools": ["write_file"],
      "status": "pending",
      "result": null
    }
  ]
}
```

**为什么不建新表**：计划是 Session 的附属状态，生命周期完全绑定在对话中。Session 删了，计划自然消失。JSON 字段足够灵活，且不需要 migration 加表。

---

## 三、核心实现：PersonalAssistant 状态分流

改动集中在 `reply_stream()` 的入口处，根据 `agent_mode` 分流到不同的处理逻辑：

```python
# agents/personal_assistant.py

async def reply_stream(self, user_input: str, images=None):
    """流式对话 — 根据 agent_mode 分流"""

    # 1. 从 DB 读取当前状态
    mode, plan_data = await self._load_mode()

    # 2. 全局逃逸：任何状态下用户都可以退出计划
    if self._is_exit_plan(user_input):
        await self._set_mode("normal", plan_data=None)
        yield {"type": "plan_cancelled"}
        # 正常回复用户
        async for event in self._normal_reply_stream(user_input, images):
            yield event
        return

    # 3. 按状态分流
    match mode:
        case "normal":
            async for event in self._handle_normal(user_input, images):
                yield event

        case "planning":
            # 用户在计划生成过程中追加了消息（当作修改意见）
            async for event in self._handle_planning(user_input, images, plan_data):
                yield event

        case "confirming":
            async for event in self._handle_confirming(user_input, plan_data):
                yield event

        case "executing":
            # 执行过程中用户插话（暂停 + 处理）
            async for event in self._handle_executing(user_input, plan_data):
                yield event

        case "step_confirm":
            async for event in self._handle_step_confirm(user_input, plan_data):
                yield event

        case _:
            # 兜底：回退正常模式
            await self._set_mode("normal")
            async for event in self._normal_reply_stream(user_input, images):
                yield event
```

---

## 四、各状态处理逻辑详解

### 4.1 NORMAL → 判断是否进入计划

```python
async def _handle_normal(self, user_input, images):
    """正常模式：判断是否需要计划，否则走原有流程"""

    # 策略 1: 用户显式触发 — "/plan xxx" 或 "先做个计划"
    if self._is_plan_trigger(user_input):
        goal = self._extract_goal(user_input)
        await self._set_mode("planning")
        async for event in self._generate_plan(goal):
            yield event
        return

    # 策略 2: 让 LLM 判断（可选，默认关闭，通过配置开启）
    # 如果 LLM 判断需要计划，返回一个建议而非直接进入
    # → Agent 回复："这个任务比较复杂，建议我先拆解个计划，你确认后再执行。
    #               你可以说「做个计划」来开始。"

    # 策略 3: 正常对话
    async for event in self._normal_reply_stream(user_input, images):
        yield event
```

### 4.2 PLANNING → 生成计划

```python
async def _generate_plan(self, goal: str):
    """调用 LLM 生成计划，然后进入 CONFIRMING"""

    yield {"type": "plan_generating", "goal": goal}

    # 构造规划专用 system prompt（注入到现有 ContextBuilder 流程）
    plan_system = await self._build_plan_system_prompt(goal)

    messages = [
        {"role": "system", "content": plan_system},
        {"role": "user", "content": f"请为以下目标制定执行计划：\n\n{goal}"},
    ]

    # 允许 Agent 调用工具来收集信息（比如先浏览网页了解情况再制定计划）
    # 复用现有的工具调用循环，但限制轮次
    plan_json = None
    for round_idx in range(8):  # 计划生成最多 8 轮
        toolkit = await self._build_active_toolkit()
        model = _build_model(self._model_config, stream=True)
        response_gen = await model(messages, tools=toolkit.get_json_schemas() if toolkit else None)

        # 流式输出思考过程
        async for chunk in response_gen:
            for block in (chunk.content or []):
                if isinstance(block, dict):
                    if block.get("type") == "thinking":
                        yield {"type": "thinking_delta", "delta": ...}

        # 如果 response 包含工具调用 → 执行后继续
        # 如果 response 是文本 → 解析为计划 JSON
        tool_calls = [...]
        if not tool_calls:
            plan_json = self._parse_plan_json(response_text)
            break
        # ... 执行工具，append 结果到 messages ...

    if plan_json:
        plan_data = {"goal": goal, "current_step": 0, **plan_json}
        await self._set_mode("confirming", plan_data)

        # 发送计划给前端展示
        yield {"type": "plan_ready", "plan": plan_data}
        yield {"type": "done"}
    else:
        await self._set_mode("normal")
        yield {"type": "text_delta", "delta": "抱歉，我无法为这个任务生成有效的计划。"}
        yield {"type": "done"}
```

### 4.3 CONFIRMING → 等待用户审批

```python
async def _handle_confirming(self, user_input: str, plan_data: dict):
    """用户审批计划：批准 / 修改 / 取消"""

    intent = self._classify_confirm_intent(user_input)
    # 可以用简单关键词匹配，也可以让 LLM 分类

    match intent:
        case "approve":
            # 进入执行
            await self._set_mode("executing", plan_data)
            async for event in self._execute_next_step(plan_data):
                yield event

        case "modify":
            # 把用户反馈传给 LLM 重新生成
            await self._set_mode("planning", plan_data)
            async for event in self._revise_plan(plan_data, user_input):
                yield event

        case "cancel":
            await self._set_mode("normal", plan_data=None)
            yield {"type": "plan_cancelled"}
            yield {"type": "text_delta", "delta": "好的，计划已取消。有什么别的可以帮你的？"}
            yield {"type": "done"}

        case _:
            # 用户可能在问计划相关的问题，让 LLM 回答
            async for event in self._answer_about_plan(user_input, plan_data):
                yield event
```

### 4.4 EXECUTING → 逐步执行

```python
async def _execute_next_step(self, plan_data: dict):
    """执行当前步骤"""

    steps = plan_data["steps"]
    idx = plan_data["current_step"]

    if idx >= len(steps):
        # 全部完成
        await self._set_mode("normal", plan_data=None)
        yield {"type": "plan_completed", "plan": plan_data}
        yield {"type": "text_delta", "delta": self._build_plan_summary(plan_data)}
        yield {"type": "done"}
        return

    step = steps[idx]
    step["status"] = "running"
    step["started_at"] = utcnow_iso()

    yield {
        "type": "plan_step_start",
        "step_index": idx,
        "step": step,
        "total_steps": len(steps),
    }

    # 关键：构造步骤专用 prompt，注入前序步骤的结果作为上下文
    step_prompt = self._build_step_prompt(step, plan_data)

    # 复用现有的 reply 核心循环（工具调用 + 流式输出）
    # 但 system prompt 要注入步骤上下文
    step_result = ""
    async for event in self._step_reply_stream(step_prompt):
        yield event  # 透传 text_delta / tool_start / tool_done 给前端
        if event.get("type") == "text_delta":
            step_result += event.get("delta", "")

    # 更新步骤状态
    step["status"] = "completed"
    step["result"] = step_result
    step["completed_at"] = utcnow_iso()
    plan_data["current_step"] = idx + 1

    yield {"type": "plan_step_done", "step_index": idx, "result": step_result}

    # 判断是否自动继续
    if plan_data.get("auto_proceed") and idx + 1 < len(steps):
        # 自动执行下一步
        await self._save_plan_data(plan_data)
        async for event in self._execute_next_step(plan_data):
            yield event
    else:
        # 暂停等待确认
        await self._set_mode("step_confirm", plan_data)
        if idx + 1 < len(steps):
            next_step = steps[idx + 1]
            yield {
                "type": "plan_step_confirm",
                "completed_step": idx,
                "next_step": {"index": idx + 1, "title": next_step["title"]},
                "message": f"步骤 {idx+1} 已完成。下一步：{next_step['title']}。继续吗？",
            }
        else:
            # 最后一步也完成了
            await self._set_mode("normal", plan_data=None)
            yield {"type": "plan_completed", "plan": plan_data}
        yield {"type": "done"}
```

### 4.5 STEP_CONFIRM → 步骤间确认

```python
async def _handle_step_confirm(self, user_input: str, plan_data: dict):
    """步骤间：继续 / 跳过 / 修改 / 取消"""

    intent = self._classify_step_intent(user_input)

    match intent:
        case "continue":
            await self._set_mode("executing", plan_data)
            async for event in self._execute_next_step(plan_data):
                yield event

        case "skip":
            idx = plan_data["current_step"]
            plan_data["steps"][idx]["status"] = "skipped"
            plan_data["current_step"] = idx + 1
            await self._set_mode("executing", plan_data)
            async for event in self._execute_next_step(plan_data):
                yield event

        case "modify":
            await self._set_mode("planning", plan_data)
            async for event in self._revise_plan(plan_data, user_input):
                yield event

        case "cancel":
            await self._set_mode("normal", plan_data=None)
            yield {"type": "plan_cancelled"}
            yield {"type": "done"}
```

---

## 五、System Prompt 注入

不改 `ContextBuilder` 的结构，只在 `_build_system_prompt` 时根据 `agent_mode` **追加一段 plan context**：

```python
async def _build_system_prompt(self, enabled_tool_names=None, skill_prompts=None):
    """原有逻辑不变，末尾追加 plan context"""
    base_prompt = ...  # 现有逻辑

    mode, plan_data = await self._load_mode()

    if mode == "planning":
        base_prompt += PLAN_GENERATION_PROMPT
    elif mode == "confirming":
        base_prompt += self._build_confirm_context(plan_data)
    elif mode in ("executing", "step_confirm"):
        base_prompt += self._build_execution_context(plan_data)

    return base_prompt
```

各阶段注入的 prompt 片段：

```python
PLAN_GENERATION_PROMPT = """
## 当前模式：计划生成

你正在为用户制定执行计划。请：
1. 分析用户的目标，拆解为 2-6 个具体步骤
2. 每步说明：标题、描述、执行策略、需要的工具
3. 你可以先调用工具（如浏览器搜索）来收集信息辅助规划
4. 最终以如下 JSON 格式输出计划（放在 ```json 代码块中）：

```json
{
  "summary": "一句话概述",
  "auto_proceed": false,
  "steps": [
    {"title": "...", "description": "...", "strategy": "...", "tools": ["..."]}
  ]
}
```
"""

def _build_execution_context(self, plan_data):
    """执行阶段注入：当前步骤 + 前序结果"""
    idx = plan_data["current_step"]
    step = plan_data["steps"][idx]
    prev_results = [
        f"- 步骤 {i+1}「{s['title']}」: {s.get('result', '已完成')}"
        for i, s in enumerate(plan_data["steps"][:idx])
        if s["status"] == "completed"
    ]
    return f"""
## 当前模式：执行计划（步骤 {idx+1}/{len(plan_data['steps'])}）

**整体目标**：{plan_data['goal']}

**前序步骤结果**：
{chr(10).join(prev_results) or '（这是第一步）'}

**当前步骤**：{step['title']}
**描述**：{step['description']}
**执行策略**：{step['strategy']}

请专注执行当前步骤，完成后给出简洁的结果总结。
"""
```

---

## 六、意图识别（轻量级）

不需要额外的 LLM 调用，用关键词 + 正则即可：

```python
class IntentClassifier:
    """轻量意图分类，用于状态转换判断"""

    PLAN_TRIGGERS = ["做个计划", "制定计划", "先规划", "/plan", "plan this"]
    EXIT_TRIGGERS = ["取消计划", "退出计划", "不要计划了", "算了", "/exit-plan"]
    APPROVE_TRIGGERS = ["批准", "执行吧", "开始", "可以", "没问题", "approve", "go", "lgtm"]
    CONTINUE_TRIGGERS = ["继续", "下一步", "next", "continue"]
    SKIP_TRIGGERS = ["跳过", "skip"]
    MODIFY_TRIGGERS = ["修改", "改一下", "调整", "不对", "modify", "revise"]

    @staticmethod
    def classify_plan_trigger(text: str) -> bool:
        t = text.strip().lower()
        return any(kw in t for kw in IntentClassifier.PLAN_TRIGGERS)

    @staticmethod
    def classify_confirm(text: str) -> str:
        """返回: approve / modify / cancel / unknown"""
        t = text.strip().lower()
        if any(kw in t for kw in IntentClassifier.APPROVE_TRIGGERS):
            return "approve"
        if any(kw in t for kw in IntentClassifier.MODIFY_TRIGGERS):
            return "modify"
        if any(kw in t for kw in IntentClassifier.EXIT_TRIGGERS):
            return "cancel"
        return "unknown"  # 交给 LLM 理解

    @staticmethod
    def classify_step(text: str) -> str:
        """返回: continue / skip / modify / cancel / unknown"""
        t = text.strip().lower()
        if any(kw in t for kw in IntentClassifier.CONTINUE_TRIGGERS):
            return "continue"
        if any(kw in t for kw in IntentClassifier.SKIP_TRIGGERS):
            return "skip"
        if any(kw in t for kw in IntentClassifier.MODIFY_TRIGGERS):
            return "modify"
        if any(kw in t for kw in IntentClassifier.EXIT_TRIGGERS):
            return "cancel"
        return "unknown"
```

对于 `unknown` 意图，**不猜测**，而是让 LLM 在当前计划上下文中自然回答用户的问题，不做状态转换。

---

## 七、SSE 事件扩展（最小集）

在现有事件基础上只增加 7 个 plan 专用事件：

```
# 现有事件（不变）
thinking_delta, text_delta, tool_start, tool_done, tool_guard_*, retry, file, done, error

# 新增 plan 事件
data: {"type": "plan_generating", "goal": "..."}
data: {"type": "plan_ready", "plan": {...}}              ← 完整计划 JSON
data: {"type": "plan_step_start", "step_index": 0, "step": {...}, "total_steps": 3}
data: {"type": "plan_step_done", "step_index": 0, "result": "..."}
data: {"type": "plan_step_confirm", "completed_step": 0, "next_step": {...}, "message": "..."}
data: {"type": "plan_completed", "plan": {...}}          ← 最终计划（含所有结果）
data: {"type": "plan_cancelled"}
```

执行步骤内部的 `text_delta` / `tool_start` / `tool_done` **照常发送**，前端通过外层的 `plan_step_start` / `plan_step_done` 来判断这些事件属于哪个步骤。

---

## 八、前端渲染（不新增页面）

全部在 ChatPage 的消息流中渲染：

```
对话消息流
│
├── [user] 帮我调研 3 个竞品并写对比报告
│
├── [assistant] plan_generating → 显示 "正在制定计划..." 动画
│
├── [plan_card]  ← 收到 plan_ready 时渲染
│   ┌─────────────────────────────────────────┐
│   │ 📋 计划：竞品调研对比报告                 │
│   │ ─────────────────────────────────────── │
│   │ ① 🔍 搜索竞品信息           ⏳ pending  │
│   │ ② 📊 整理对比数据           ⏳ pending  │
│   │ ③ 📝 撰写分析报告           ⏳ pending  │
│   │ ─────────────────────────────────────── │
│   │ [✅ 开始执行]  [✏️ 修改]  [❌ 取消]      │
│   └─────────────────────────────────────────┘
│
├── [user] 开始执行
│
├── [plan_card]  ← 收到 plan_step_start 时更新同一个卡片
│   ┌─────────────────────────────────────────┐
│   │ ① 🔍 搜索竞品信息           🔄 运行中   │  ← 展开显示 tool_start/tool_done
│   │    ├── 🔧 browser_use("搜索竞品A")      │
│   │    └── 🔧 browser_use("搜索竞品B")      │
│   │ ② 📊 整理对比数据           ⏳ pending  │
│   │ ③ 📝 撰写分析报告           ⏳ pending  │
│   └─────────────────────────────────────────┘
│
├── [assistant] 步骤 1 已完成。下一步：整理对比数据。继续吗？
│
├── [user] 继续
│   ...
```

前端只需一个 `PlanCard` 组件，通过 SSE 事件实时更新状态即可。

---

## 九、状态持久化与恢复

```python
async def _load_mode(self) -> tuple[str, dict | None]:
    """从 DB 读取当前 agent_mode 和 plan_data"""
    if self._db is None:
        return "normal", None
    result = await self._db.execute(
        select(SessionRecord.agent_mode, SessionRecord.plan_data)
        .where(SessionRecord.id == self.session_id)
    )
    row = result.first()
    if row is None:
        return "normal", None
    return row[0] or "normal", row[1]

async def _set_mode(self, mode: str, plan_data: dict | None = _UNSET):
    """更新 agent_mode（和可选的 plan_data）到 DB"""
    if self._db is None:
        return
    updates = {"agent_mode": mode}
    if plan_data is not _UNSET:
        updates["plan_data"] = plan_data
    await self._db.execute(
        update(SessionRecord)
        .where(SessionRecord.id == self.session_id)
        .values(**updates)
    )
    await self._db.flush()
```

**恢复场景**：用户关浏览器再打开，前端拉 `/sessions/{id}/meta` → 发现 `agent_mode != normal` → 渲染计划卡片 + 进度。用户发消息 → 后端从 DB 读到状态，无缝继续。

---

## 十、改动文件清单

```
backend/agentpal/
├── models/session.py              +2 字段 (agent_mode, plan_data)
├── agents/
│   ├── personal_assistant.py      reply_stream() 入口分流 + 5 个 handler
│   ├── intent.py                  ← 新文件，IntentClassifier
│   └── plan_prompts.py            ← 新文件，各阶段 prompt 模板
├── api/v1/endpoints/session.py    meta 接口返回 agent_mode + plan_data
└── (无新增 API 路由)

frontend/src/
├── components/PlanCard.tsx        ← 新组件
├── pages/ChatPage.tsx             处理 plan_* SSE 事件
└── hooks/usePlan.ts               ← 新 hook，管理 plan 状态
```

---

## 十一、与现有模块的关系

| 现有模块 | Plan Mode 怎么用 |
|----------|-----------------|
| `reply_stream()` 工具循环 | 每个 step 复用它执行，只是 system prompt 不同 |
| `_build_system_prompt()` | 末尾追加 plan context，ContextBuilder 不改 |
| `ToolGuard` | 步骤执行中照常触发，不特殊处理 |
| `Memory` | 计划生成/执行过程的对话正常写入 memory |
| `SubAgent` | Phase 2 可以让步骤指定 `agent_type: "sub_agent:researcher"`，派遣执行 |
| `SessionRecord` | 状态机的载体，加两个字段就够 |

---

## 十二、关键设计原则

1. **零新 API** — 所有交互走 `/chat`，前端不需要学新接口
2. **状态在 DB** — `agent_mode` + `plan_data` 持久化在 SessionRecord，重启可恢复
3. **逃逸优先** — 任何状态下用户说"取消/退出"都能回到正常模式
4. **复用循环** — 不重写工具调用逻辑，每个步骤就是一次带上下文的 `reply_stream`
5. **渐进实现** — Phase 1 只做 NORMAL↔PLANNING↔CONFIRMING↔EXECUTING，STEP_CONFIRM 可以后加

---

## 十三、实现路径（分阶段）

### Phase 1 (MVP)
- SessionRecord +2 字段 + DB migration
- `IntentClassifier` 关键词匹配
- `reply_stream()` 状态分流骨架
- `_generate_plan()` + `_execute_next_step()` 核心流程
- 4 个 plan SSE 事件
- 前端 PlanCard 只读展示 + 审批按钮
- 单元测试

### Phase 2 — 交互增强
- 步骤内联编辑（前端）
- `revise`（用户反馈 → LLM 修改计划）
- `STEP_CONFIRM` 状态 + skip/retry
- `auto_proceed` 模式
- 计划生成阶段允许工具调用（先搜索再规划）

### Phase 3 — 高级特性
- 步骤派遣给 SubAgent 执行
- 步骤依赖 DAG（depends_on → 拓扑排序 → 并行执行）
- 计划级 ToolGuard 预审批
- 计划模板（保存 → 复用）
- 计划完成后自动写入长期记忆摘要

---

## 待讨论

- [ ] 意图识别是否需要 LLM 辅助？还是纯关键词够用？
- [ ] `auto_proceed` 默认开还是关？
- [ ] 计划生成阶段是否允许工具调用？（Phase 1 还是 Phase 2？）
- [ ] 步骤执行失败时的策略：自动重试 / 暂停等用户 / 跳过继续？
- [ ] plan_data 长期增长问题：完成的计划是清掉还是归档到 memory？
- [ ] 前端 PlanCard 按钮操作是直接发聊天消息，还是走单独的 action（仍然通过 /chat 但带特殊 prefix）？
