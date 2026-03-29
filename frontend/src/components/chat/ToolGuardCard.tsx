import clsx from "clsx";
import { Shield, ShieldAlert, ShieldCheck, ShieldX, CheckCircle2, XCircle } from "lucide-react";
import type { ToolGuardRequest } from "../../types/chat";
import { LEVEL_LABELS } from "../../utils/chatHelpers";

export default function ToolGuardCard({
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
