  AgentPal Prompt 优化建议 — 对标Claude Code System Prompts

  基于对 Piebald-AI/claude-code-system-prompts 的深入研究，以下是 AgentPal 可以借鉴的 prompt 工程和架构优化方向：

  ---
  一、Prompt 模块化与组合机制

  Claude Code 做法： 将 system prompt 拆成~50+ 个独立 markdown 文件，每个文件有YAML front matter（name, description, ccVersion, 模板变量如 ${BASH_TOOL_NAME}），运行时按角色/模式/工具动态组装。

  AgentPal 现状： ContextBuilder 已有分段组装（SOUL/AGENTS/MEMORY 等），但各段内容写死在 defaults.py 或 workspace 文件中，缺乏模板变量和条件注入机制。

  建议：
  1. 引入 prompt fragment 注册机制 — 将 defaults.py 中的大段文本拆成独立 .md 文件（如 prompts/tool-usage-rules.md、prompts/output-format.md），每个文件带front matter 标注适用场景（PA/Sub/Cron）和优先级
  2. 支持条件注入 — 根据当前模式（普通对话/plan mode/tool guard 激活）动态加载不同 prompt 片段，而非在 _build_system_prompt() 中用 if/else 拼接
  3. 模板变量 — 将 {session_id}、{model_name}、{guard_threshold} 等运行时值通过统一的模板引擎注入，而非字符串拼接

  ---
  二、工具使用规范 Prompt

  Claude Code 做法： 有专门的 system-prompt-tool-usage-*.md 系列（~10 个文件），逐工具规定：
  - 何时用 Read vs cat、Edit vs sed、Glob vs find
  - 工具调用前必须先读文件
  - 不要在 Bash 中做能用专用工具做的事
  - 子Agent 委派的边界（何时用 Explore agent vs直接 Grep）

  AgentPal 现状： AGENTS.md 中有工具路由规则，但偏宏观（"安全工具默认开启，危险工具需确认"），缺乏逐工具的使用指南。

  建议：
  1. 为每个内置工具编写使用指南 prompt — 特别是 execute_shell_command（何时该用、何时不该用）、browser_use（使用场景和限制）、dispatch_sub_agent（路由决策逻辑）
  2. 在 system prompt 中注入工具使用最佳实践 — 例如："调用 write_file 前必须先read_file 确认目标文件状态"、"优先用 edit_file 而非 write_file 修改已有文件"
  3. 工具互斥/优先级规则 — 明确告诉 LLM 工具之间的选择逻辑，减少不必要的工具调用

  ---
  三、输出效率与格式控制

  Claude Code 做法： 专门的 system-prompt-output-efficiency.md 强制：
  - 直奔主题，不要重述用户说的话
  - 跳过填充词和不必要的过渡
  - 只在需要用户决策、里程碑状态更新、错误/阻塞时输出文本
  - "一句话能说清的不要用三句"

  AgentPal 现状： SOUL.md 定义了语气和价值观，但缺乏对输出效率的硬性约束。

  建议：
  1. 添加 output-efficiency prompt段 — 在 ContextBuilder 中加入输出格式约束，控制 LLM 的冗余输出
  2. 区分对话模式和任务模式的输出风格 — 闲聊时可以友好，执行工具任务时应简洁直接
  3. 禁止特定模式 — 如 "不要以'好的'、'当然'开头"、"不要在每次工具调用后总结刚做了什么"

  ---
  四、安全与谨慎行动Prompt

  Claude Code 做法： 多层安全 prompt：
  - doing-tasks-security.md — 不引入XSS/SQL 注入等漏洞
  - auto-mode.md — 自主执行时仍避免破坏性操作
  - 详细的"可逆性评估"指令— 区分本地可逆操作 vs 影响共享系统的操作
  - 明确列举需要确认的高危操作类型

  AgentPal 现状： Tool Guard 机制很好（分级0-4），但 prompt 层面的安全指导偏简单，主要依赖 Tool Guard 的硬拦截。

  建议：
  1. 在 system prompt 中加入"行动前评估"指令 — 让 LLM 在调用工具前自行评估操作的可逆性和影响范围，而非完全依赖 Tool Guard 的正则匹配
  2. 添加"破坏性操作清单" — 明确列出哪些操作需要额外谨慎（删除文件、执行 rm、修改配置文件等），让 LLM 主动提醒用户
  3. SubAgent 安全边界 — 在 SubAgent 的 role_prompt 中明确其权限范围，防止越权操作

  ---
  五、记忆系统 Prompt 优化

  Claude Code 做法：
  - agent-prompt-dream-memory-consolidation.md — 四阶段记忆整理（定向→收集→合并→修剪）
  - 记忆分类型（user/feedback/project/reference），每种有明确的保存时机和使用场景
  - 严格区分"该存记忆的"和"不该存的"（代码模式、git 历史、调试方案不存）
  - 记忆文件有front matter（name/description/type），索引文件有行数限制

  AgentPal 现状： MemoryWriter 用_EXTRACT_PROMPT 提取事实，但提取策略较粗放，没有记忆分类和整理机制。

  建议：
  1. 引入记忆分类 — 将提取的记忆分为：用户偏好、项目上下文、反馈修正、外部引用，不同类型有不同的保留策略和使用优先级
  2. 记忆整理（Dream）机制 — 定期（或达到阈值时）运行记忆合并，将重复/过时的记忆条目合并或删除，保持 MEMORY.md 精简
  3. 记忆验证 — 在使用记忆前验证其时效性（"记忆说X 存在"不等于"X 现在存在"），特别是涉及文件路径、函数名等可能已变更的信息
  4. 优化 _EXTRACT_PROMPT — 加入"不该提取什么"的负面指令（不提取代码片段、不提取临时调试信息）

  ---
  六、SubAgent 委派与协作 Prompt

  Claude Code 做法：
  - system-prompt-fork-usage-guidelines.md — 明确何时该/不该 fork 子 Agent
  - 禁止偷看子 Agent 中间输出、禁止编造子 Agent 结果
  - 子 Agent 完成后必须检查结果再继续

  AgentPal 现状： PA 通过 dispatch_sub_agent 工具委派，AGENTS.md 有路由规则，但缺乏委派决策的细粒度指导。

  建议：
  1. 添加委派决策 prompt — 明确告诉 PA："简单任务自己做，只在以下情况委派 SubAgent：(1) 需要专业知识 (2) 任务耗时长 (3) 需要隔离上下文"
  2. 结果验证指令 — PA 收到 SubAgent 结果后应验证而非盲目转发给用户
  3. SubAgent 工作原则强化 — 在 _build_sub_system_prompt() 中加入：完成后必须产出明确结果、遇到阻塞主动上报、不要做超出任务范围的事

  ---
  七、Plan Mode Prompt 优化

  Claude Code 做法：
  - 四阶段 plan mode，每阶段有独立 prompt
  - Phase 4 强制：计划文件不超过 40 行、用bullet points 不用散文、必须包含验证命令
  - 明确区分"探索阶段"和"实施阶段"

  AgentPal 现状： plans/prompts.py 有PLAN_GENERATION_PROMPT 等，但格式约束较松。

  建议：
  1. 加入计划格式硬约束 — 限制计划长度、要求每步包含预期输出和验证方法
  2. 分阶段 prompt注入 — 探索阶段鼓励广泛搜索，实施阶段要求精确操作
  3. 计划修订机制 — 当执行偏离计划时，prompt 应指导 LLM 更新计划而非静默偏离

  ---
  八、Scope Creep 防护

  Claude Code 做法： 多个专门的 prompt 防止过度工程：
  - no-unnecessary-additions — 不加未要求的功能
  - no-premature-abstractions — 三行重复代码好过一个过早抽象
  - no-compatibility-hacks — 不加向后兼容 shim
  - no-unnecessary-error-handling — 只在系统边界做校验

  AgentPal 现状： 缺乏此类约束，LLM 可能在执行任务时过度发挥。

  建议：
  1. 在AGENTS.md 或新增 prompt 段中加入反scope-creep 规则 — 特别是 coder SubAgent，应明确"只做被要求的事"
  2. 工具调用节制 — 提示 LLM 评估每次工具调用的必要性，避免"为了确认而确认"的冗余调用

  ---
  九、System Reminder 条件注入机制

  Claude Code 做法： 有一系列 system-reminder-*.md，在特定条件下注入（如 token 预算告警、任务工具提醒、plan mode 通知），而非始终加载。

  AgentPal 现状： 所有 prompt 段在每次对话都完整加载，缺乏条件触发的轻量提醒机制。

  建议：
  1. 实现 system reminder 机制 — 在特定条件下注入短提醒（如 token 接近上限时提醒压缩、长时间未使用工具时提醒可用工具）
  2. 减少常驻 prompt 体积 — 将不常用的指令移到条件注入，降低每次请求的 token 消耗

  ---
  十、Prompt 版本管理与可观测性

  Claude Code 做法： 每个 prompt 文件带 ccVersion 标记，CHANGELOG.md 记录每次变更。

  AgentPal 现状： prompt 内容在代码中，变更随代码提交，缺乏独立的 prompt 版本追踪。

  建议：
  1. 将 prompt 模板从代码中分离 — 放到 backend/agentpal/prompts/ 目录，每个文件独立版本管理
  2. 记录 prompt 变更日志 — 方便追踪哪次prompt 修改导致了行为变化
  3. Prompt 效果可观测 — 在 LLMCallLog 中记录使用的 system prompt 版本/hash，便于 A/B 测试

  ---
  优先级排序

  ┌────────┬───────────────────────┬─────────────────────────────────────┐
  │ 优先级 │        优化项         │              预期收益               │
  ├────────┼───────────────────────┼─────────────────────────────────────┤
  │ P0     │ 输出效率 prompt       │ 立即减少冗余输出，提升用户体验      │
  ├────────┼───────────────────────┼─────────────────────────────────────┤
  │ P0     │ 工具使用规范 prompt   │ 减少错误工具调用，降低 token 消耗   │
  ├────────┼───────────────────────┼─────────────────────────────────────┤
  │ P1     │ Scope creep 防护      │ 防止 Agent 过度发挥，提升任务精准度 │
  ├────────┼───────────────────────┼─────────────────────────────────────┤
  │ P1     │ 安全行动评估 prompt   │ 补充 Tool Guard 的 prompt层防护     │
  ├────────┼───────────────────────┼─────────────────────────────────────┤
  │ P1     │ 记忆分类与整理        │ 提升长期记忆质量，减少噪音          │
  ├────────┼───────────────────────┼─────────────────────────────────────┤
  │ P2     │ Prompt 模块化重构     │ 提升可维护性，支持按场景组合        │
  ├────────┼───────────────────────┼─────────────────────────────────────┤
  │ P2     │ 条件注入 reminder     │ 减少 token 消耗，提升上下文利用率   │
  ├────────┼───────────────────────┼─────────────────────────────────────┤
  │ P2     │ SubAgent 委派决策优化 │ 减少不必要的委派，提升效率          │
  ├────────┼───────────────────────┼─────────────────────────────────────┤
  │ P3     │ Plan mode 格式硬约束  │ 提升计划质量和可执行性              │
  ├────────┼───────────────────────┼─────────────────────────────────────┤
  │ P3     │ Prompt 版本管理       │ 长期可维护性和可观测性              │
  └────────┴───────────────────────┴─────────────────────────────────────┘