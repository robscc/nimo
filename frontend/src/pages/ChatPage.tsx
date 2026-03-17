import { useState, useRef, useEffect, useCallback } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  Send, Trash2, Wrench, ChevronDown, ChevronRight,
  CheckCircle2, Loader2, XCircle, Paperclip, Brain,
  Info, Settings, Cpu, Puzzle, X, Smartphone,
  Code2, Terminal, CalendarClock, Shield, ShieldAlert,
  ShieldCheck, ShieldX,
} from "lucide-react";
import clsx from "clsx";
import { clearMemory, createSession, getSessions, getSessionMessages, resolveToolGuard } from "../api";
import NimoIcon from "../components/NimoIcon";
import SessionPanel from "../components/SessionPanel";
import { useSessionMeta, useUpdateSessionConfig } from "../hooks/useSessionMeta";
import { useTools } from "../hooks/useTools";
import { useSkills } from "../hooks/useSkills";
import { useSessionEvents } from "../hooks/useSessionEvents";

// ── Types ─────────────────────────────────────────────────

interface ToolCallEntry {
  id: string;
  name: string;
  input: Record<string, unknown>;
  output?: string;
  error?: string | null;
  duration_ms?: number;
  status: "running" | "done" | "cancelled";
}

interface FileAttachment {
  url: string;
  name: string;
  mime: string;
}

interface ToolGuardRequest {
  requestId: string;
  toolName: string;
  toolInput: Record<string, unknown>;
  level: number;
  rule: string | null;
  threshold: number;
  description: string;
  status: "pending" | "approved" | "rejected";
}

interface RetryEntry {
  attempt: number;
  maxAttempts: number;
  error: string;
  delay: number;
}

interface Message {
  role: "user" | "assistant";
  content: string;
  thinking?: string;
  streamingThinking?: boolean;
  toolCalls?: ToolCallEntry[];
  files?: FileAttachment[];
  streaming?: boolean;
  guardRequest?: ToolGuardRequest;
  retries?: RetryEntry[];
}

// ── Thinking Bubble ────────────────────────────────────────

function ThinkingBubble({ content, streaming }: { content: string; streaming?: boolean }) {
  const [collapsed, setCollapsed] = useState(false);

  // 思考完成后 1.5s 自动折叠
  useEffect(() => {
    if (!streaming && content) {
      const t = setTimeout(() => setCollapsed(true), 1500);
      return () => clearTimeout(t);
    }
  }, [streaming, content]);

  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50/80 text-xs overflow-hidden">
      <button
        onClick={() => setCollapsed((c) => !c)}
        className="w-full flex items-center gap-1.5 px-3 py-1.5 text-left hover:bg-gray-100 transition-colors"
      >
        {streaming
          ? <Loader2 size={11} className="text-gray-400 animate-spin shrink-0" />
          : <Brain size={11} className="text-gray-400 shrink-0" />}
        <span className="text-gray-500 font-medium">思考过程</span>
        {streaming && <span className="text-gray-400 animate-pulse ml-0.5">…</span>}
        <span className="ml-auto text-gray-400 shrink-0">
          {collapsed
            ? <ChevronRight size={11} />
            : <ChevronDown size={11} />}
        </span>
      </button>
      {!collapsed && (
        <div className="px-3 pt-2 pb-2.5 border-t border-gray-200 text-gray-500 leading-relaxed whitespace-pre-wrap max-h-52 overflow-y-auto">
          {content}
          {streaming && (
            <span className="inline-block w-0.5 h-[1em] bg-gray-400 ml-0.5 align-middle animate-pulse" />
          )}
        </div>
      )}
    </div>
  );
}

// ── Shell Output Renderer ──────────────────────────────────

type ShellParsed =
  | { type: "error"; message: string }
  | { type: "result"; returncode: number | null; stdout: string; stderr: string };

function parseShellOutput(raw: string): ShellParsed {
  const errorMatch = raw.match(/<error>([\s\S]*?)<\/error>/);
  if (errorMatch) return { type: "error", message: errorMatch[1].trim() };

  const rc = raw.match(/<returncode>(\d+)<\/returncode>/);
  const stdout = raw.match(/<stdout>([\s\S]*?)<\/stdout>/)?.[1] ?? "";
  const stderr = raw.match(/<stderr>([\s\S]*?)<\/stderr>/)?.[1] ?? "";
  return {
    type: "result",
    returncode: rc ? parseInt(rc[1], 10) : null,
    stdout: stdout.trim(),
    stderr: stderr.trim(),
  };
}

function ShellOutput({ command, raw }: { command: string; raw: string }) {
  const parsed = parseShellOutput(raw);

  if (parsed.type === "error") {
    return (
      <div className="rounded-lg bg-gray-950 text-xs font-mono overflow-hidden">
        <div className="flex items-center gap-2 px-3 py-2 bg-gray-800">
          <span className="text-gray-500">$</span>
          <span className="text-gray-300 flex-1 truncate">{command}</span>
        </div>
        <p className="px-3 py-2 text-red-400 whitespace-pre-wrap">{parsed.message}</p>
      </div>
    );
  }

  const ok = parsed.returncode === 0;

  return (
    <div className="rounded-lg bg-gray-950 text-xs font-mono overflow-hidden">
      {/* header */}
      <div className="flex items-center gap-2 px-3 py-2 bg-gray-800">
        <span className="text-gray-500">$</span>
        <span className="text-gray-300 flex-1 truncate">{command}</span>
        {parsed.returncode !== null && (
          <span className={clsx(
            "px-1.5 py-0.5 rounded text-[10px] font-semibold shrink-0",
            ok ? "bg-green-900/50 text-green-400" : "bg-red-900/50 text-red-400",
          )}>
            {ok ? "exit 0" : `exit ${parsed.returncode}`}
          </span>
        )}
      </div>
      {/* stdout */}
      {parsed.stdout && (
        <pre className="px-3 py-2.5 text-gray-300 overflow-x-auto max-h-72 whitespace-pre-wrap leading-relaxed">
          {parsed.stdout}
        </pre>
      )}
      {/* stderr */}
      {parsed.stderr && (
        <pre className={clsx(
          "px-3 py-2.5 overflow-x-auto max-h-40 whitespace-pre-wrap leading-relaxed",
          parsed.stdout && "border-t border-gray-800",
          ok ? "text-amber-400" : "text-red-400",
        )}>
          {parsed.stderr}
        </pre>
      )}
    </div>
  );
}

// ── Python Code Output ─────────────────────────────────────

function PythonCodeOutput({
  code,
  packages,
  raw,
}: {
  code: string;
  packages?: string[];
  raw: string;
}) {
  const parsed = parseShellOutput(raw);
  const lines = code.split("\n");

  if (parsed.type === "error") {
    return (
      <div className="rounded-lg text-xs font-mono overflow-hidden">
        <div className="flex items-center gap-2 px-3 py-2 bg-[#181825]">
          <Code2 size={11} className="text-blue-400 shrink-0" />
          <span className="text-[#7f849c]">python</span>
        </div>
        <pre className="bg-[#1e1e2e] px-3 py-2.5 text-red-400 whitespace-pre-wrap">{parsed.message}</pre>
      </div>
    );
  }

  const ok = parsed.returncode === 0;
  const hasOutput = !!(parsed.stdout || parsed.stderr);

  return (
    <div className="rounded-lg text-xs font-mono overflow-hidden shadow-sm border border-white/5">
      {/* ── header ── */}
      <div className="flex items-center gap-2 px-3 py-2 bg-[#181825]">
        <Code2 size={11} className="text-blue-400 shrink-0" />
        <span className="text-[#7f849c] text-[10px]">python</span>
        {packages && packages.length > 0 && (
          <>
            <span className="text-[#45475a] text-[10px] select-none">·</span>
            <span className="text-[#7f849c] text-[10px]">pip install {packages.join(" ")}</span>
          </>
        )}
        {parsed.returncode !== null && (
          <span className={clsx(
            "ml-auto px-1.5 py-0.5 rounded text-[10px] font-semibold shrink-0",
            ok ? "bg-green-900/40 text-green-400" : "bg-red-900/40 text-red-400",
          )}>
            {ok ? "✓ 成功" : `exit ${parsed.returncode}`}
          </span>
        )}
      </div>

      {/* ── code block ── */}
      <div className="bg-[#1e1e2e] flex overflow-x-auto">
        {/* line numbers */}
        <div className="select-none py-3 px-3 text-right text-[#45475a] shrink-0 leading-[1.65]">
          {lines.map((_, i) => (
            <div key={i}>{i + 1}</div>
          ))}
        </div>
        {/* code text */}
        <pre className="flex-1 py-3 pr-4 text-[#cdd6f4] whitespace-pre leading-[1.65] min-w-0 overflow-x-auto">
          {code}
        </pre>
      </div>

      {/* ── output section ── */}
      {hasOutput && (
        <>
          <div className="flex items-center gap-1.5 px-3 py-1.5 bg-[#13131a] border-t border-white/5">
            <Terminal size={9} className="text-[#45475a]" />
            <span className="text-[9px] text-[#45475a] uppercase tracking-widest select-none">输出</span>
          </div>
          <div className="bg-[#0d0d14]">
            {parsed.stdout && (
              <pre className="px-3 py-2.5 text-[#cdd6f4] overflow-x-auto max-h-64 whitespace-pre-wrap leading-[1.65]">
                {parsed.stdout}
              </pre>
            )}
            {parsed.stderr && (
              <pre className={clsx(
                "px-3 py-2.5 overflow-x-auto max-h-40 whitespace-pre-wrap leading-[1.65]",
                parsed.stdout ? "border-t border-white/5" : "",
                ok ? "text-amber-400" : "text-red-400",
              )}>
                {parsed.stderr}
              </pre>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ── Tool Guard Card ─────────────────────────────────────────

const LEVEL_LABELS: Record<number, { label: string; color: string; bg: string; border: string }> = {
  0: { label: "毁灭性", color: "text-red-700", bg: "bg-red-50", border: "border-red-200" },
  1: { label: "高危", color: "text-orange-700", bg: "bg-orange-50", border: "border-orange-200" },
  2: { label: "中危", color: "text-amber-700", bg: "bg-amber-50", border: "border-amber-200" },
  3: { label: "低危", color: "text-blue-700", bg: "bg-blue-50", border: "border-blue-200" },
  4: { label: "安全", color: "text-green-700", bg: "bg-green-50", border: "border-green-200" },
};

function ToolGuardCard({
  guard,
  onResolve,
}: {
  guard: ToolGuardRequest;
  onResolve: (requestId: string, approved: boolean) => void;
}) {
  const levelInfo = LEVEL_LABELS[guard.level] ?? LEVEL_LABELS[0];
  const isPending = guard.status === "pending";

  // Format the key input parameter for display
  const inputPreview = (() => {
    if (guard.toolName === "execute_shell_command") return guard.toolInput.command as string;
    if (guard.toolName === "write_file" || guard.toolName === "edit_file") return guard.toolInput.file_path as string;
    if (guard.toolName === "execute_python_code") {
      const code = (guard.toolInput.code as string) ?? "";
      return code.split("\n").find((l) => l.trim())?.trim().slice(0, 80) ?? "...";
    }
    return JSON.stringify(guard.toolInput).slice(0, 80);
  })();

  return (
    <div className={clsx(
      "rounded-xl border-2 text-xs overflow-hidden transition-all",
      isPending ? `${levelInfo.border} ${levelInfo.bg}` : "border-gray-200 bg-gray-50 opacity-75",
    )}>
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-black/5">
        {isPending ? (
          <ShieldAlert size={16} className={levelInfo.color} />
        ) : guard.status === "approved" ? (
          <ShieldCheck size={16} className="text-green-500" />
        ) : (
          <ShieldX size={16} className="text-red-500" />
        )}
        <span className="font-semibold text-gray-700">安全确认</span>
        <span className={clsx(
          "px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider",
          levelInfo.bg, levelInfo.color, levelInfo.border, "border",
        )}>
          Level {guard.level} · {levelInfo.label}
        </span>
        {!isPending && (
          <span className={clsx(
            "ml-auto px-2 py-0.5 rounded-full text-[10px] font-semibold",
            guard.status === "approved" ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700",
          )}>
            {guard.status === "approved" ? "已确认" : "已取消"}
          </span>
        )}
      </div>

      {/* Body */}
      <div className="px-4 py-3 space-y-2">
        <div className="flex items-start gap-2">
          <span className="text-gray-500 shrink-0 w-10">工具:</span>
          <span className="font-mono font-medium text-gray-800">{guard.toolName}</span>
        </div>
        <div className="flex items-start gap-2">
          <span className="text-gray-500 shrink-0 w-10">参数:</span>
          <pre className="font-mono text-gray-700 break-all whitespace-pre-wrap flex-1 min-w-0 bg-white/60 rounded px-2 py-1 border border-black/5">
            {inputPreview}
          </pre>
        </div>
        {guard.rule && (
          <div className="flex items-start gap-2">
            <span className="text-gray-500 shrink-0 w-10">规则:</span>
            <span className="font-mono text-gray-600">{guard.rule}</span>
          </div>
        )}
        <div className={clsx("text-[11px] mt-1 px-2 py-1.5 rounded-lg", levelInfo.bg)}>
          <Shield size={11} className={clsx("inline mr-1", levelInfo.color)} />
          此操作安全等级为 <strong>{guard.level}</strong>（{levelInfo.label}），当前会话阈值为 <strong>{guard.threshold}</strong>
        </div>
      </div>

      {/* Actions */}
      {isPending && (
        <div className="flex items-center gap-3 px-4 py-3 border-t border-black/5 bg-white/50">
          <button
            onClick={() => onResolve(guard.requestId, true)}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-nimo-500 text-white text-xs font-semibold hover:bg-nimo-600 transition-colors shadow-sm"
          >
            <CheckCircle2 size={13} />
            确认执行
          </button>
          <button
            onClick={() => onResolve(guard.requestId, false)}
            className="flex items-center gap-1.5 px-4 py-2 rounded-lg border border-gray-300 text-gray-600 text-xs font-semibold hover:bg-gray-100 transition-colors"
          >
            <XCircle size={13} />
            取消
          </button>
        </div>
      )}
    </div>
  );
}

// ── Tool Call Card ─────────────────────────────────────────

function ToolCallCard({ tc }: { tc: ToolCallEntry }) {
  const [open, setOpen] = useState(false);
  const hasError = !!tc.error;
  const isPython = tc.name === "execute_python_code";

  // For python tool: show first non-blank line of code as preview
  const inputPreview = isPython
    ? ((tc.input.code as string) ?? "").split("\n").find((l) => l.trim())?.trim().slice(0, 60) ?? "…"
    : JSON.stringify(tc.input).slice(0, 60);

  return (
    <div className={clsx(
      "rounded-lg border text-xs overflow-hidden",
      hasError ? "border-red-200 bg-red-50/60" : "border-nimo-100 bg-nimo-50/50",
    )}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-black/5 transition-colors"
      >
        {tc.status === "running" ? (
          <Loader2 size={12} className="text-nimo-500 animate-spin shrink-0" />
        ) : hasError ? (
          <XCircle size={12} className="text-red-400 shrink-0" />
        ) : (
          <CheckCircle2 size={12} className="text-green-500 shrink-0" />
        )}
        {isPython
          ? <Code2 size={12} className={clsx("shrink-0", hasError ? "text-red-400" : "text-blue-400")} />
          : <Wrench size={12} className={clsx("shrink-0", hasError ? "text-red-400" : "text-nimo-400")} />}
        <span className={clsx("font-mono font-medium", hasError ? "text-red-700" : "text-nimo-600")}>
          {tc.name}
        </span>
        {tc.status === "running" ? (
          <span className="text-nimo-400 text-xs">执行中…</span>
        ) : (
          <span className="text-gray-400 truncate flex-1 text-left ml-1 font-mono">
            {inputPreview}
          </span>
        )}
        {tc.duration_ms !== undefined && (
          <span className="text-gray-400 shrink-0 ml-auto">{tc.duration_ms}ms</span>
        )}
        {open
          ? <ChevronDown size={12} className="text-gray-400 shrink-0" />
          : <ChevronRight size={12} className="text-gray-400 shrink-0" />}
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-2 border-t border-black/5">
          {isPython ? (
            // Python: unified code + output view (skip raw JSON "输入参数")
            <div className="pt-2">
              <PythonCodeOutput
                code={(tc.input.code as string) ?? ""}
                packages={tc.input.packages as string[] | undefined}
                raw={tc.output ?? ""}
              />
            </div>
          ) : (
            // Default: raw input params + tool-specific output
            <>
              <div className="pt-2">
                <p className="text-gray-500 mb-1 font-medium">输入参数</p>
                <pre className="bg-white rounded p-2 border text-gray-700 overflow-x-auto whitespace-pre-wrap">
                  {JSON.stringify(tc.input, null, 2)}
                </pre>
              </div>
              {tc.output && (
                <div>
                  {tc.name === "execute_shell_command" ? (
                    <ShellOutput command={tc.input.command as string} raw={tc.output} />
                  ) : (
                    <>
                      <p className={clsx("mb-1 font-medium", hasError ? "text-red-500" : "text-gray-500")}>
                        {hasError ? "错误" : "输出结果"}
                      </p>
                      <pre className={clsx(
                        "rounded p-2 border overflow-x-auto max-h-48 whitespace-pre-wrap text-xs",
                        hasError ? "bg-red-50 border-red-100 text-red-600" : "bg-white text-gray-700",
                      )}>
                        {tc.output}
                      </pre>
                    </>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── Session Meta Panel ───────────────────────────────────────

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

function SessionMetaPanel({
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
      </div>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────

function mapHistoryToMessages(history: Awaited<ReturnType<typeof getSessionMessages>>) {
  return history.map((m) => {
    const meta = m.meta;
    return {
      role: m.role as "user" | "assistant",
      content: m.content,
      thinking: meta?.thinking,
      toolCalls: meta?.tool_calls?.map((tc) => ({
        id: tc.id,
        name: tc.name,
        input: tc.input,
        output: tc.output,
        error: tc.error,
        duration_ms: tc.duration_ms,
        status: "done" as const,
      })),
      files: meta?.files,
    };
  });
}

export default function ChatPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const sessionIdParam = searchParams.get("session");
  const [sessionId, setSessionId] = useState<string | null>(sessionIdParam);
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  const [showMeta, setShowMeta] = useState(false);

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();

  const isReadOnly = sessionId?.startsWith("dingtalk:") ?? false;

  // Tool Guard resolve handler
  const handleGuardResolve = async (requestId: string, approved: boolean) => {
    try {
      await resolveToolGuard(requestId, approved);
    } catch (err) {
      console.error("Failed to resolve tool guard:", err);
    }
  };

  // 实时接收定时任务推送的 session 消息
  const handleRemoteMessage = useCallback(
    (event: { type: "new_message"; message: { id: string; role: string; content: string; created_at: string | null } }) => {
      const { message } = event;
      if (message.role !== "assistant" && message.role !== "user") return;
      setMessages((prev) => [
        ...prev,
        {
          role: message.role as "user" | "assistant",
          content: message.content,
        },
      ]);
    },
    []
  );
  useSessionEvents(sessionId, handleRemoteMessage);

  // 初始化：优先加载 URL 中的 session，否则加载最新 session，否则新建
  useEffect(() => {
    (async () => {
      if (sessionIdParam) {
        // URL 已指定 session：直接加载其消息
        const history = await getSessionMessages(sessionIdParam);
        setMessages(mapHistoryToMessages(history));
      } else {
        // 无 URL 参数：加载最近一条 session，没有才新建
        const sessions = await getSessions();
        if (sessions.length > 0) {
          const latest = sessions[0];
          setSessionId(latest.id);
          setSearchParams({ session: latest.id }, { replace: true });
          const history = await getSessionMessages(latest.id);
          setMessages(mapHistoryToMessages(history));
        } else {
          const { id } = await createSession();
          setSessionId(id);
          setSearchParams({ session: id }, { replace: true });
        }
      }
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // 切换到历史 session
  const handleSelectSession = async (id: string) => {
    abortRef.current?.abort();
    setIsStreaming(false);
    setSessionId(id);
    setSearchParams({ session: id });
    const history = await getSessionMessages(id);
    setMessages(mapHistoryToMessages(history));
  };

  // 新建对话回调
  const handleNewSession = (id: string) => {
    abortRef.current?.abort();
    setIsStreaming(false);
    setSessionId(id);
    setMessages([]);
    setSearchParams({ session: id });
    queryClient.invalidateQueries({ queryKey: ["sessions"] });
  };

  const sendMessage = async (text: string) => {
    if (!text.trim() || isStreaming || !sessionId) return;
    setInput("");
    setIsStreaming(true);

    // 一次性追加 user + assistant 占位消息
    setMessages((prev) => [
      ...prev,
      { role: "user", content: text },
      { role: "assistant", content: "", toolCalls: [], streaming: true },
    ]);

    abortRef.current = new AbortController();

    try {
      const resp = await fetch("/api/v1/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, message: text, channel: "web" }),
        signal: abortRef.current.signal,
      });

      if (!resp.ok || !resp.body) {
        throw new Error(`HTTP ${resp.status}`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      const updateLast = (updater: (m: Message) => Message) =>
        setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? updater(m) : m)));

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const ev = JSON.parse(line.slice(6)) as Record<string, unknown>;

          if (ev.type === "thinking_delta") {
            updateLast((m) => ({
              ...m,
              thinking: (m.thinking ?? "") + (ev.delta as string),
              streamingThinking: true,
            }));
          } else if (ev.type === "tool_start") {
            updateLast((m) => ({
              ...m,
              toolCalls: [
                ...(m.toolCalls ?? []),
                {
                  id: ev.id as string,
                  name: ev.name as string,
                  input: ev.input as Record<string, unknown>,
                  status: "running" as const,
                },
              ],
            }));
          } else if (ev.type === "tool_done") {
            updateLast((m) => ({
              ...m,
              toolCalls: (m.toolCalls ?? []).map((tc) =>
                tc.id === ev.id
                  ? {
                      ...tc,
                      output: ev.output as string,
                      error: ev.error as string | null,
                      duration_ms: ev.duration_ms as number,
                      status: "done" as const,
                    }
                  : tc,
              ),
            }));
          } else if (ev.type === "text_delta") {
            updateLast((m) => ({
              ...m,
              content: m.content + (ev.delta as string),
              streamingThinking: false,
            }));
          } else if (ev.type === "file") {
            updateLast((m) => ({
              ...m,
              files: [
                ...(m.files ?? []),
                { url: ev.url as string, name: ev.name as string, mime: ev.mime as string },
              ],
            }));
          } else if (ev.type === "tool_guard_request") {
            updateLast((m) => ({
              ...m,
              guardRequest: {
                requestId: ev.request_id as string,
                toolName: ev.tool_name as string,
                toolInput: ev.tool_input as Record<string, unknown>,
                level: ev.level as number,
                rule: (ev.rule as string) || null,
                threshold: ev.threshold as number,
                description: (ev.description as string) || "",
                status: "pending",
              },
            }));
          } else if (ev.type === "tool_guard_waiting") {
            // 心跳，不需特殊处理
          } else if (ev.type === "tool_guard_resolved") {
            updateLast((m) => ({
              ...m,
              guardRequest: m.guardRequest
                ? { ...m.guardRequest, status: (ev.approved as boolean) ? "approved" : "rejected" }
                : undefined,
            }));
          } else if (ev.type === "retry") {
            updateLast((m) => ({
              ...m,
              retries: [
                ...(m.retries ?? []),
                {
                  attempt: ev.attempt as number,
                  maxAttempts: ev.max_attempts as number,
                  error: ev.error as string,
                  delay: ev.delay as number,
                },
              ],
            }));
          } else if (ev.type === "done") {
            updateLast((m) => ({ ...m, streaming: false }));
            // 刷新会话列表（更新 title 和 message_count）
            queryClient.invalidateQueries({ queryKey: ["sessions"] });
          } else if (ev.type === "error") {
            updateLast((m) => ({
              ...m,
              content: `\u26a0\ufe0f ${ev.message as string}`,
              streaming: false,
            }));
          }
        }
      }
    } catch (err) {
      if (err instanceof Error && err.name !== "AbortError") {
        setMessages((prev) =>
          prev.map((m, i) =>
            i === prev.length - 1
              ? { ...m, content: "\u26a0\ufe0f 请求失败，请检查网络或稍后重试", streaming: false }
              : m,
          ),
        );
      }
    } finally {
      setIsStreaming(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    sendMessage(input.trim());
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !file.name.endsWith(".zip")) return;

    setMessages((prev) => [
      ...prev,
      { role: "user", content: `📦 上传技能包: ${file.name}` },
      { role: "assistant", content: "", toolCalls: [], streaming: true },
    ]);
    setIsStreaming(true);

    try {
      const form = new FormData();
      form.append("file", file);
      const resp = await fetch("/api/v1/skills/install/zip", {
        method: "POST",
        body: form,
      });
      const data = await resp.json();

      if (resp.ok) {
        setMessages((prev) =>
          prev.map((m, i) =>
            i === prev.length - 1
              ? {
                  ...m,
                  content: `✅ 技能 **${data.name}** v${data.version} 安装成功！\n\n包含 ${data.tools.length} 个工具: ${data.tools.join(", ")}`,
                  streaming: false,
                }
              : m,
          ),
        );
      } else {
        setMessages((prev) =>
          prev.map((m, i) =>
            i === prev.length - 1
              ? { ...m, content: `⚠️ 安装失败: ${data.detail || "未知错误"}`, streaming: false }
              : m,
          ),
        );
      }
    } catch {
      setMessages((prev) =>
        prev.map((m, i) =>
          i === prev.length - 1
            ? { ...m, content: "⚠️ 上传失败，请检查网络", streaming: false }
            : m,
        ),
      );
    } finally {
      setIsStreaming(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  // 清空当前对话并创建新 session
  const handleClear = async () => {
    abortRef.current?.abort();
    if (sessionId) await clearMemory(sessionId);
    const { id } = await createSession();
    setSessionId(id);
    setMessages([]);
    setSearchParams({ session: id });
    queryClient.invalidateQueries({ queryKey: ["sessions"] });
  };

  return (
    <div className="flex h-full">
      {/* Session Panel */}
      <SessionPanel
        currentSessionId={sessionId}
        onSelectSession={handleSelectSession}
        onNewSession={handleNewSession}
        collapsed={panelCollapsed}
        onToggleCollapse={() => setPanelCollapsed((v) => !v)}
      />

      {/* Chat Area */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-3 bg-white border-b">
          <h1 className="font-semibold text-gray-800 flex items-center gap-2">
            <NimoIcon size={20} />
            <span>nimo</span>
          </h1>
          <div className="flex items-center gap-1">
          {!isReadOnly && (
          <button
            onClick={handleClear}
            className="p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
            title="清空对话"
          >
            <Trash2 size={18} />
          </button>
          )}
          {!isReadOnly && sessionId && (
          <Link
            to={`/cron?from_session=${encodeURIComponent(sessionId)}`}
            className="p-2 text-gray-400 hover:text-nimo-500 hover:bg-nimo-50 rounded-lg transition-colors"
            title="新建定时任务（通知到此会话）"
          >
            <CalendarClock size={18} />
          </Link>
          )}
          {!isReadOnly && (
          <button
            onClick={() => setShowMeta((v) => !v)}
            className={clsx(
              "p-2 rounded-lg transition-colors",
              showMeta
                ? "text-nimo-500 bg-nimo-50"
                : "text-gray-400 hover:text-gray-600 hover:bg-gray-100"
            )}
            title="会话信息"
          >
            <Settings size={18} />
          </button>
          )}
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-4">
              <NimoIcon size={64} />
              <p className="text-lg text-gray-500">嗨！我是 nimo，你的智能助手 🐠</p>
            </div>
          )}

          {messages.map((msg, i) => (
            <div
              key={i}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              {msg.role === "assistant" ? (
                <div className="max-w-[75%] space-y-1.5 min-w-0">
                  {/* Thinking bubble */}
                  {msg.thinking && (
                    <ThinkingBubble content={msg.thinking} streaming={msg.streamingThinking} />
                  )}
                  {/* Tool Guard confirmation */}
                  {msg.guardRequest && (
                    <ToolGuardCard guard={msg.guardRequest} onResolve={handleGuardResolve} />
                  )}
                  {/* Tool call cards */}
                  {(msg.toolCalls ?? []).map((tc) => (
                    <ToolCallCard key={tc.id} tc={tc} />
                  ))}
                  {/* 文件附件 */}
                  {(msg.files ?? []).map((f, fi) =>
                    f.mime.startsWith("image/") ? (
                      <img
                        key={fi}
                        src={f.url}
                        alt={f.name}
                        className="max-w-full rounded-xl border shadow-sm max-h-80 object-contain"
                      />
                    ) : (
                      <a
                        key={fi}
                        href={f.url}
                        download={f.name}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-center gap-2 px-3 py-2 rounded-lg border bg-white text-sm text-nimo-600 hover:bg-nimo-50 transition-colors"
                      >
                        <Paperclip size={14} /> {f.name}
                      </a>
                    )
                  )}
                  {/* Retry status */}
                  {(msg.retries ?? []).length > 0 && (
                    <div className="rounded-lg border border-amber-200 bg-amber-50/80 text-xs overflow-hidden">
                      <div className="flex items-center gap-1.5 px-3 py-1.5 text-amber-700 font-medium">
                        <Loader2 size={12} className={msg.streaming ? "animate-spin" : ""} />
                        <span>LLM 调用重试</span>
                      </div>
                      <div className="px-3 pb-2 space-y-0.5">
                        {(msg.retries ?? []).map((r, ri) => (
                          <div key={ri} className="flex items-center gap-1.5 text-amber-600">
                            <XCircle size={10} className="shrink-0 text-amber-500" />
                            <span>
                              第 {r.attempt}/{r.maxAttempts} 次失败：{r.error}
                              {msg.streaming && ri === (msg.retries?.length ?? 0) - 1
                                ? `，${r.delay}s 后重试…`
                                : ""}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {/* Text bubble — show if has content OR still streaming */}
                  {(msg.content || msg.streaming) && (
                    <div className="bg-white border rounded-2xl px-4 py-2.5 text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">
                      {msg.content || (
                        <span className="text-gray-400 animate-pulse">思考中…</span>
                      )}
                      {/* Blinking cursor */}
                      {msg.streaming && msg.content && (
                        <span className="inline-block w-0.5 h-[1em] bg-nimo-500 ml-0.5 align-middle animate-pulse" />
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <div className="max-w-[70%] bg-nimo-500 text-white rounded-2xl px-4 py-2.5 text-sm leading-relaxed">
                  {msg.content}
                </div>
              )}
            </div>
          ))}

          <div ref={bottomRef} />
        </div>

        {/* Input */}
        {isReadOnly ? (
          <div className="px-6 py-4 bg-white border-t flex items-center justify-center gap-2 text-gray-400 text-sm">
            <Smartphone size={16} className="text-blue-400" />
            <span>钉钉会话（只读）— 仅供查看历史消息</span>
          </div>
        ) : (
        <form onSubmit={handleSubmit} className="px-6 py-4 bg-white border-t flex gap-3">
          <label
            className="w-10 h-10 rounded-xl border border-gray-200 text-gray-400 hover:text-nimo-500 hover:border-nimo-300 flex items-center justify-center cursor-pointer transition-colors shrink-0"
            title="上传技能包 (.zip)"
          >
            <Paperclip size={18} />
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip"
              className="hidden"
              onChange={handleFileUpload}
              disabled={isStreaming}
            />
          </label>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="输入消息...（支持拖入 .zip 技能包）"
            className="flex-1 rounded-xl border border-gray-200 px-4 py-2 text-sm outline-none focus:border-nimo-400 transition-colors"
            disabled={isStreaming}
          />
          <button
            type="submit"
            disabled={isStreaming || !input.trim()}
            className="w-10 h-10 rounded-xl bg-nimo-500 text-white flex items-center justify-center hover:bg-nimo-600 disabled:opacity-40 transition-colors"
          >
            <Send size={18} />
          </button>
        </form>
        )}
      </div>

      {/* Session Meta Panel */}
      {showMeta && sessionId && !isReadOnly && (
        <SessionMetaPanel
          sessionId={sessionId}
          onClose={() => setShowMeta(false)}
        />
      )}
    </div>
  );
}
