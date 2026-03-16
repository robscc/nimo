import { useState } from "react";
import {
  Terminal, FileText, FilePlus, FileEdit, Globe, Clock, Puzzle,
  AlertTriangle, CheckCircle2, XCircle, ChevronDown, ChevronRight,
  Loader2, RefreshCw, Zap,
} from "lucide-react";
import clsx from "clsx";
import { useTools, useToggleTool, useToolLogs, type ToolLog } from "../hooks/useTools";

// ── 图标映射 ──────────────────────────────────────────────
const ICON_MAP: Record<string, React.ComponentType<{ size?: number | string; className?: string }>> = {
  Terminal, FileText, FilePlus, FileEdit, Globe, Clock, Puzzle,
};

// ── Toggle Switch ─────────────────────────────────────────
function Toggle({ enabled, onChange, disabled }: {
  enabled: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={() => !disabled && onChange(!enabled)}
      disabled={disabled}
      className={clsx(
        "relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none",
        enabled ? "bg-nimo-500" : "bg-gray-200",
        disabled && "opacity-50 cursor-not-allowed"
      )}
    >
      <span
        className={clsx(
          "inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform",
          enabled ? "translate-x-6" : "translate-x-1"
        )}
      />
    </button>
  );
}

// ── 调用日志行 ────────────────────────────────────────────
function LogRow({ log }: { log: ToolLog }) {
  const [open, setOpen] = useState(false);
  const hasError = !!log.error;
  const time = new Date(log.created_at).toLocaleTimeString("zh-CN");

  return (
    <div className="border-b border-gray-100 last:border-0">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-gray-50 text-left transition-colors"
      >
        {hasError
          ? <XCircle size={14} className="text-red-400 shrink-0" />
          : <CheckCircle2 size={14} className="text-green-400 shrink-0" />}

        <span className="text-xs font-mono text-nimo-500 w-36 shrink-0">{log.tool_name}</span>
        <span className="text-xs text-gray-500 truncate flex-1">
          {JSON.stringify(log.input).slice(0, 80)}
        </span>
        <span className="text-xs text-gray-400 shrink-0">{log.duration_ms ?? "\u2014"}ms</span>
        <span className="text-xs text-gray-400 w-20 text-right shrink-0">{time}</span>
        {open ? <ChevronDown size={14} className="text-gray-400 shrink-0" />
               : <ChevronRight size={14} className="text-gray-400 shrink-0" />}
      </button>
      {open && (
        <div className="px-4 pb-3 bg-gray-50 space-y-2">
          <div>
            <p className="text-xs font-medium text-gray-500 mb-1">输入参数</p>
            <pre className="text-xs bg-white border rounded p-2 overflow-x-auto text-gray-700">
              {JSON.stringify(log.input, null, 2)}
            </pre>
          </div>
          {log.output && (
            <div>
              <p className="text-xs font-medium text-gray-500 mb-1">输出结果</p>
              <pre className="text-xs bg-white border rounded p-2 overflow-x-auto text-gray-700 max-h-40">
                {log.output}
              </pre>
            </div>
          )}
          {log.error && (
            <div>
              <p className="text-xs font-medium text-red-500 mb-1">错误</p>
              <pre className="text-xs bg-red-50 border border-red-100 rounded p-2 text-red-600">
                {log.error}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── 主页面 ────────────────────────────────────────────────
export default function ToolsPage() {
  const [selectedTool, setSelectedTool] = useState<string | undefined>();
  const { data: tools = [], isLoading } = useTools();
  const { data: logs = [], refetch: refetchLogs, isFetching } = useToolLogs(selectedTool);
  const toggle = useToggleTool();

  const enabledCount = tools.filter((t) => t.enabled).length;

  return (
    <div className="flex h-full overflow-hidden">
      {/* ── 左侧：工具列表 ── */}
      <div className="w-80 border-r bg-white flex flex-col shrink-0">
        {/* Header */}
        <div className="px-5 py-4 border-b">
          <div className="flex items-center justify-between">
            <h1 className="font-semibold text-gray-800 flex items-center gap-2">
              <Zap size={18} className="text-nimo-500" />
              工具管理
            </h1>
            <span className="text-xs bg-nimo-100 text-nimo-600 px-2 py-0.5 rounded-full font-medium">
              {enabledCount}/{tools.length} 已启用
            </span>
          </div>
          <p className="text-xs text-gray-400 mt-1">开关工具后立即生效，无需重启</p>
        </div>

        {/* Tool list */}
        <div className="flex-1 overflow-y-auto py-2">
          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 size={20} className="animate-spin text-gray-300" />
            </div>
          ) : (
            tools.map((tool) => {
              const Icon = ICON_MAP[tool.icon] ?? Zap;
              const isSelected = selectedTool === tool.name;
              return (
                <div
                  key={tool.name}
                  onClick={() => setSelectedTool(isSelected ? undefined : tool.name)}
                  className={clsx(
                    "mx-2 mb-1 rounded-xl p-3 cursor-pointer transition-colors",
                    isSelected
                      ? "bg-nimo-50 ring-1 ring-nimo-200"
                      : "hover:bg-gray-50"
                  )}
                >
                  <div className="flex items-start gap-3">
                    {/* Icon */}
                    <div className={clsx(
                      "w-9 h-9 rounded-lg flex items-center justify-center shrink-0",
                      tool.enabled ? "bg-nimo-100 text-nimo-500" : "bg-gray-100 text-gray-400"
                    )}>
                      <Icon size={18} />
                    </div>

                    {/* Info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className={clsx(
                          "text-sm font-medium truncate",
                          tool.enabled ? "text-gray-800" : "text-gray-400"
                        )}>
                          {tool.name}
                        </span>
                        {tool.dangerous && (
                          <span title="危险工具"><AlertTriangle size={12} className="text-amber-400 shrink-0" /></span>
                        )}
                      </div>
                      <p className="text-xs text-gray-400 mt-0.5 truncate">{tool.description}</p>
                    </div>

                    {/* Toggle */}
                    <Toggle
                      enabled={tool.enabled}
                      disabled={toggle.isPending}
                      onChange={(enabled) =>
                        toggle.mutate({ name: tool.name, enabled })
                      }
                    />
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Footer hint */}
        <div className="px-4 py-3 border-t bg-amber-50">
          <p className="text-xs text-amber-600 flex items-center gap-1.5">
            <AlertTriangle size={12} />
            危险工具（Shell、写文件）默认关闭，请谨慎启用
          </p>
        </div>
      </div>

      {/* ── 右侧：调用日志 ── */}
      <div className="flex-1 flex flex-col overflow-hidden bg-gray-50">
        {/* Header */}
        <div className="px-5 py-4 bg-white border-b flex items-center justify-between">
          <div>
            <h2 className="font-medium text-gray-800">
              {selectedTool ? (
                <>调用日志 · <span className="text-nimo-500 font-mono text-sm">{selectedTool}</span></>
              ) : (
                "所有工具调用日志"
              )}
            </h2>
            <p className="text-xs text-gray-400 mt-0.5">每 5 秒自动刷新 · 最近 50 条</p>
          </div>
          <button
            onClick={() => refetchLogs()}
            disabled={isFetching}
            className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-700 px-3 py-1.5 rounded-lg hover:bg-gray-100 transition-colors"
          >
            <RefreshCw size={13} className={isFetching ? "animate-spin" : ""} />
            刷新
          </button>
        </div>

        {/* Log list */}
        <div className="flex-1 overflow-y-auto">
          {logs.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-3">
              <Zap size={32} className="text-gray-200" />
              <div className="text-center">
                <p className="text-sm font-medium">暂无调用记录</p>
                <p className="text-xs mt-1">
                  {selectedTool ? `${selectedTool} 尚未被调用` : "启用工具后，在对话中触发工具调用即可看到记录"}
                </p>
              </div>
            </div>
          ) : (
            <div className="bg-white mx-4 my-4 rounded-xl border shadow-sm overflow-hidden">
              {/* Table header */}
              <div className="flex items-center gap-3 px-4 py-2 bg-gray-50 border-b text-xs font-medium text-gray-400">
                <span className="w-4" />
                <span className="w-36">工具名</span>
                <span className="flex-1">参数预览</span>
                <span className="w-12 text-right">耗时</span>
                <span className="w-20 text-right">时间</span>
                <span className="w-4" />
              </div>
              {logs.map((log) => <LogRow key={log.id} log={log} />)}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
