import clsx from "clsx";
import {
  Brain, Cpu, Info, Loader2, Puzzle, Settings, Shield, Users,
  Wrench, X,
} from "lucide-react";
import { useSessionMeta, useUpdateSessionConfig } from "../../hooks/useSessionMeta";
import { useTools } from "../../hooks/useTools";
import { useSkills } from "../../hooks/useSkills";

function MetaStatCard({ icon: Icon, label, value, sub }: {
  icon: React.ComponentType<any>;
  label: string;
  value: string | number;
  sub?: string;
}) {
  return (
    <div className="bg-gradient-to-br from-gray-50 to-gray-100/50 rounded-lg px-3 py-2.5 border border-gray-100">
      <div className="flex items-center gap-1.5 mb-1">
        <Icon size={11} className="text-gray-400" />
        <span className="text-[10px] font-medium text-gray-400 uppercase tracking-wider">{label}</span>
      </div>
      <p className="text-sm font-semibold text-gray-800">{value}</p>
      {sub && <p className="text-[10px] text-gray-400 mt-0.5">{sub}</p>}
    </div>
  );
}

export default function SessionMetaPanel({
  sessionId,
  onClose,
}: {
  sessionId: string;
  onClose: () => void;
}) {
  const { data: meta, isLoading } = useSessionMeta(sessionId);
  const { data: allTools = [] } = useTools();
  const { data: allSkills = [] } = useSkills();
  const updateConfig = useUpdateSessionConfig();

  const globalEnabledTools = allTools.filter((t) => t.enabled).map((t) => t.name);
  const globalEnabledSkills = allSkills.filter((s) => s.enabled).map((s) => s.name);

  // session 配置的工具，null = 跟随全局
  const sessionTools = meta?.enabled_tools;
  const sessionSkills = meta?.enabled_skills;
  const effectiveTools = sessionTools ?? globalEnabledTools;
  const effectiveSkills = sessionSkills ?? globalEnabledSkills;

  const toggleTool = (toolName: string) => {
    const current = sessionTools ?? [...globalEnabledTools];
    const next = current.includes(toolName)
      ? current.filter((t) => t !== toolName)
      : [...current, toolName];
    updateConfig.mutate({ sessionId, config: { enabled_tools: next } });
  };

  const toggleSkill = (skillName: string) => {
    const current = sessionSkills ?? [...globalEnabledSkills];
    const next = current.includes(skillName)
      ? current.filter((s) => s !== skillName)
      : [...current, skillName];
    updateConfig.mutate({ sessionId, config: { enabled_skills: next } });
  };

  const resetToGlobal = () => {
    updateConfig.mutate({
      sessionId,
      config: { enabled_tools: null, enabled_skills: null },
    });
  };

  const createdTime = meta?.created_at
    ? new Date(meta.created_at).toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : "—";
  const updatedTime = meta?.updated_at
    ? new Date(meta.updated_at).toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : "—";

  if (isLoading) {
    return (
      <div className="w-80 border-l bg-white flex items-center justify-center">
        <Loader2 size={20} className="animate-spin text-gray-300" />
      </div>
    );
  }

  return (
    <div data-testid="session-meta-panel" className="w-80 border-l bg-white flex flex-col shrink-0 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b bg-gradient-to-r from-nimo-50/50 to-white">
        <span className="text-sm font-semibold text-gray-700 flex items-center gap-1.5">
          <Info size={14} className="text-nimo-500" />
          会话信息
        </span>
        <button
          onClick={onClose}
          className="w-6 h-6 flex items-center justify-center rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100"
        >
          <X size={14} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-5">
        {/* Model badge */}
        <div className="flex items-center gap-3 p-3 rounded-xl bg-gradient-to-r from-nimo-50 to-nimo-100/30 border border-nimo-100">
          <div className="w-10 h-10 rounded-lg bg-white border border-nimo-200 flex items-center justify-center shadow-sm">
            <Cpu size={18} className="text-nimo-500" />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-[10px] font-medium text-nimo-400 uppercase tracking-wider">模型</p>
            <p className="text-sm font-semibold text-gray-800 font-mono truncate">{meta?.model_name || "默认"}</p>
          </div>
        </div>

        {/* Stats grid */}
        <div className="grid grid-cols-2 gap-2">
          <MetaStatCard icon={Brain} label="消息数" value={meta?.message_count ?? 0} />
          <MetaStatCard icon={Settings} label="Tokens" value={meta?.context_tokens ?? "—"} />
          <MetaStatCard icon={Info} label="创建" value={createdTime} />
          <MetaStatCard icon={Info} label="活跃" value={updatedTime} />
        </div>

        {/* Session ID */}
        <div className="px-3 py-2 rounded-lg bg-gray-50 border border-gray-100">
          <p className="text-[10px] font-medium text-gray-400 uppercase tracking-wider mb-1">Session ID</p>
          <p className="text-[11px] font-mono text-gray-500 break-all select-all leading-relaxed">{sessionId}</p>
        </div>

        {/* Divider */}
        <div className="border-t border-gray-100" />

        {/* Tools toggle */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-xs font-semibold text-gray-600 flex items-center gap-1.5">
              <Wrench size={13} className="text-nimo-500" /> 工具
              <span className="text-[10px] font-normal px-1.5 py-0.5 rounded-full bg-nimo-100 text-nimo-600">
                {effectiveTools.length}/{allTools.length}
              </span>
            </p>
            {sessionTools !== null && (
              <button
                onClick={resetToGlobal}
                className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 hover:bg-gray-200 transition-colors"
              >
                重置全局
              </button>
            )}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {allTools.map((tool) => {
              const enabled = effectiveTools.includes(tool.name);
              const globalEnabled = globalEnabledTools.includes(tool.name);
              return (
                <button
                  key={tool.name}
                  onClick={() => globalEnabled && toggleTool(tool.name)}
                  disabled={!globalEnabled}
                  className={clsx(
                    "inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-mono transition-all border",
                    !globalEnabled
                      ? "border-gray-100 bg-gray-50 text-gray-300 cursor-not-allowed line-through"
                      : enabled
                        ? "border-nimo-200 bg-nimo-50 text-nimo-700 shadow-sm hover:shadow"
                        : "border-gray-200 bg-white text-gray-400 hover:border-gray-300 hover:text-gray-600"
                  )}
                >
                  <span className={clsx(
                    "w-1.5 h-1.5 rounded-full shrink-0",
                    !globalEnabled ? "bg-gray-200" : enabled ? "bg-nimo-400" : "bg-gray-300"
                  )} />
                  {tool.name}
                </button>
              );
            })}
          </div>
        </div>

        {/* Skills toggle */}
        {allSkills.length > 0 && (
          <div className="space-y-2">
            <p className="text-xs font-semibold text-gray-600 flex items-center gap-1.5">
              <Puzzle size={13} className="text-nimo-500" /> 技能
              <span className="text-[10px] font-normal px-1.5 py-0.5 rounded-full bg-nimo-100 text-nimo-600">
                {effectiveSkills.length}/{allSkills.length}
              </span>
            </p>
            <div className="flex flex-wrap gap-1.5">
              {allSkills.map((skill) => {
                const enabled = effectiveSkills.includes(skill.name);
                const globalEnabled = globalEnabledSkills.includes(skill.name);
                return (
                  <button
                    key={skill.name}
                    onClick={() => globalEnabled && toggleSkill(skill.name)}
                    disabled={!globalEnabled}
                    className={clsx(
                      "inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] transition-all border",
                      !globalEnabled
                        ? "border-gray-100 bg-gray-50 text-gray-300 cursor-not-allowed line-through"
                        : enabled
                          ? "border-nimo-200 bg-nimo-50 text-nimo-700 shadow-sm hover:shadow"
                          : "border-gray-200 bg-white text-gray-400 hover:border-gray-300 hover:text-gray-600"
                    )}
                  >
                    <span className={clsx(
                      "w-1.5 h-1.5 rounded-full shrink-0",
                      !globalEnabled ? "bg-gray-200" : enabled ? "bg-nimo-400" : "bg-gray-300"
                    )} />
                    {skill.name}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {/* Tool Guard threshold */}
        <div className="space-y-2">
          <div className="border-t border-gray-100 pt-3" />
          <p className="text-xs font-semibold text-gray-600 flex items-center gap-1.5">
            <Shield size={13} className="text-nimo-500" /> 安全阈值
            <span className="text-[10px] font-normal px-1.5 py-0.5 rounded-full bg-nimo-100 text-nimo-600">
              {meta?.tool_guard_threshold ?? "全局"}
            </span>
          </p>
          <p className="text-[10px] text-gray-400 leading-relaxed">
            安全等级低于阈值的工具调用需用户确认（0=毁灭性 → 4=安全）
          </p>
          <div className="flex gap-1.5">
            {[0, 1, 2, 3, 4].map((level) => {
              const isActive = (meta?.tool_guard_threshold ?? null) === level;
              const labels: Record<number, string> = { 0: "全放行", 1: "仅拦截毁灭性", 2: "拦截高危+", 3: "拦截中危+", 4: "全部拦截" };
              return (
                <button
                  key={level}
                  onClick={() =>
                    updateConfig.mutate({ sessionId, config: { tool_guard_threshold: level } })
                  }
                  className={clsx(
                    "px-2 py-1.5 rounded-lg text-[10px] font-mono font-semibold transition-all border",
                    isActive
                      ? "border-nimo-300 bg-nimo-100 text-nimo-700 shadow-sm"
                      : "border-gray-200 bg-white text-gray-400 hover:border-gray-300 hover:text-gray-600"
                  )}
                  title={labels[level]}
                >
                  {level}
                </button>
              );
            })}
            {meta?.tool_guard_threshold !== null && meta?.tool_guard_threshold !== undefined && (
              <button
                onClick={() =>
                  updateConfig.mutate({ sessionId, config: { tool_guard_threshold: null } })
                }
                className="px-2 py-1.5 rounded-lg text-[10px] border border-gray-200 bg-white text-gray-400 hover:border-gray-300 hover:text-gray-600 transition-all"
                title="重置为全局默认"
              >
                重置
              </button>
            )}
          </div>
        </div>

        {/* SubAgent dispatch mode */}
        <div data-testid="sub-agent-mode" className="space-y-2">
          <div className="border-t border-gray-100 pt-3" />
          <p className="text-xs font-semibold text-gray-600 flex items-center gap-1.5">
            <Users size={13} className="text-nimo-500" /> SubAgent 调度模式
          </p>
          <p className="text-[10px] text-gray-400 leading-relaxed">
            控制 LLM 何时将任务委派给专业 SubAgent
          </p>
          <div className="flex gap-1.5">
            {([
              { value: null, label: "全局", testId: "sub-agent-btn-global" },
              { value: "auto" as const, label: "自动", testId: "sub-agent-btn-auto" },
              { value: "manual" as const, label: "手动", testId: "sub-agent-btn-manual" },
              { value: "off" as const, label: "关", testId: "sub-agent-btn-off" },
            ] as const).map((opt) => {
              const currentMode = meta?.sub_agent_mode ?? null;
              const isActive = currentMode === opt.value;
              return (
                <button
                  key={opt.testId}
                  data-testid={opt.testId}
                  aria-pressed={isActive}
                  onClick={() =>
                    updateConfig.mutate({ sessionId, config: { sub_agent_mode: opt.value } })
                  }
                  className={clsx(
                    "px-2.5 py-1.5 rounded-lg text-[10px] font-semibold transition-all border",
                    isActive
                      ? "border-nimo-300 bg-nimo-100 text-nimo-700 shadow-sm"
                      : "border-gray-200 bg-white text-gray-400 hover:border-gray-300 hover:text-gray-600"
                  )}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
          <p data-testid="sub-agent-status" className="text-[10px] text-gray-500">
            {meta?.sub_agent_mode === null || meta?.sub_agent_mode === undefined
              ? "当前: 跟随全局"
              : meta.sub_agent_mode === "auto"
                ? "当前: 自动模式"
                : meta.sub_agent_mode === "manual"
                  ? "当前: 手动模式(@mention)"
                  : "当前: 已禁用"}
          </p>
          {(meta?.sub_agent_mode === "manual" || (meta?.sub_agent_mode == null)) && (
            <p className="text-[10px] text-gray-400 bg-gray-50 rounded-lg px-2.5 py-1.5 border border-gray-100">
              手动模式下用 @researcher 或 @coder 指定 SubAgent
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
