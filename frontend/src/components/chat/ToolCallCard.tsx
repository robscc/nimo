import { useState } from "react";
import clsx from "clsx";
import { Wrench, Loader2, CheckCircle2, XCircle, ChevronDown, ChevronRight, Code2, Bot } from "lucide-react";
import type { ToolCallEntry } from "../../types/chat";
import ShellOutput from "./ShellOutput";
import PythonCodeOutput from "./PythonCodeOutput";

export default function ToolCallCard({ tc }: { tc: ToolCallEntry }) {
  const [open, setOpen] = useState(false);
  const hasError = !!tc.error;
  const isPython = tc.name === "execute_python_code";
  const isDispatch = tc.name === "dispatch_sub_agent";

  const dispatchAgent = isDispatch
    ? String((tc.input.agent_name as string | undefined) ?? "").trim() || "default"
    : "";

  const dispatchBadgeStyle =
    dispatchAgent === "coder"
      ? "bg-emerald-100 text-emerald-700 border-emerald-200"
      : dispatchAgent === "researcher"
        ? "bg-blue-100 text-blue-700 border-blue-200"
        : dispatchAgent === "ops-engineer"
          ? "bg-violet-100 text-violet-700 border-violet-200"
          : "bg-gray-100 text-gray-700 border-gray-200";

  const dispatchInitial =
    dispatchAgent === "coder"
      ? "C"
      : dispatchAgent === "researcher"
        ? "R"
        : dispatchAgent === "ops-engineer"
          ? "O"
          : "D";

  const dispatchSessionId = isDispatch
    ? String((tc.input.parent_session_id as string | undefined) ?? "").trim()
    : "";

  const dispatchTooltip = isDispatch
    ? [
        `Agent: ${dispatchAgent}`,
        `Session: ${dispatchSessionId || "(unknown)"}`,
        "",
        "Task Prompt:",
        String((tc.input.task_prompt as string | undefined) ?? "").trim() || "(empty)",
      ].join("\n")
    : "";

  const dispatchPreview = isDispatch
    ? String((tc.input.task_prompt as string | undefined) ?? "").replace(/\s+/g, " ").trim().slice(0, 80) || "派发任务"
    : "";

  // For python tool: show first non-blank line of code as preview
  const inputPreview = isDispatch
    ? dispatchPreview
    : isPython
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
          : isDispatch
            ? <Bot size={12} className={clsx("shrink-0", hasError ? "text-red-400" : "text-indigo-500")} />
            : <Wrench size={12} className={clsx("shrink-0", hasError ? "text-red-400" : "text-nimo-400")} />}
        <span className={clsx("font-mono font-medium", hasError ? "text-red-700" : isDispatch ? "text-indigo-700" : "text-nimo-600")}>
          {isDispatch ? "dispatch_sub_agent" : tc.name}
        </span>
        {isDispatch && (
          <span
            className={clsx("inline-flex items-center gap-1.5 px-1.5 py-0.5 rounded border text-[10px] font-medium", dispatchBadgeStyle)}
            title={dispatchTooltip}
          >
            <span className={clsx(
              "w-4 h-4 rounded-full flex items-center justify-center text-[9px] font-bold border",
              dispatchBadgeStyle,
            )}>
              {dispatchInitial}
            </span>
            <span>派发给 {dispatchAgent}</span>
          </span>
        )}
        {tc.status === "running" ? (
          <span className={clsx("text-xs", isDispatch ? "text-indigo-500" : "text-nimo-400")}>
            {isDispatch ? "派发中…" : "执行中…"}
          </span>
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
                  ) : isDispatch ? (
                    <>
                      <p className={clsx("mb-1 font-medium", hasError ? "text-red-500" : "text-indigo-600")}>
                        {hasError ? "派发失败" : "派发结果"}
                      </p>
                      <pre className={clsx(
                        "rounded p-2 border overflow-x-auto max-h-48 whitespace-pre-wrap text-xs",
                        hasError
                          ? "bg-red-50 border-red-100 text-red-600"
                          : "bg-indigo-50 border-indigo-100 text-indigo-700",
                      )}>
                        {tc.output}
                      </pre>
                    </>
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
