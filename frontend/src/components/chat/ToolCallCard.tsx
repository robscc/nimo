import { useState } from "react";
import clsx from "clsx";
import { Wrench, Loader2, CheckCircle2, XCircle, ChevronDown, ChevronRight, Code2 } from "lucide-react";
import type { ToolCallEntry } from "../../types/chat";
import ShellOutput from "./ShellOutput";
import PythonCodeOutput from "./PythonCodeOutput";

export default function ToolCallCard({ tc }: { tc: ToolCallEntry }) {
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
