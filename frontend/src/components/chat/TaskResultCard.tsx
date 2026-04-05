import { useState } from "react";
import clsx from "clsx";
import { CheckCircle2, ChevronDown, ChevronRight, Users, CalendarClock } from "lucide-react";
import type { Message } from "../../types/chat";

export default function TaskResultCard({ msg }: { msg: Message }) {
  const [collapsed, setCollapsed] = useState(false);
  const isSubAgent = msg.cardType === "sub_agent_result";
  const isCron = msg.cardType === "cron_result";

  const agentName = (msg.cardMeta?.agent_name as string) || (isSubAgent ? "SubAgent" : "Cron");
  const taskPrompt = (msg.cardMeta?.task_prompt as string) || "";
  const taskId = msg.cardMeta?.task_id as string | undefined;
  const jobName = msg.cardMeta?.job_name as string | undefined;

  const accentColor = isSubAgent
    ? {
        border: "border-blue-200",
        bg: "bg-blue-50",
        icon: "text-blue-600",
        hover: "hover:bg-blue-50",
      }
    : {
        border: "border-purple-200",
        bg: "bg-purple-50",
        icon: "text-purple-600",
        hover: "hover:bg-purple-50",
      };

  return (
    <div className="max-w-full rounded-lg border bg-white shadow-sm overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setCollapsed(!collapsed)}
        className={clsx(
          "w-full flex items-center gap-2.5 px-3.5 py-2.5 text-left transition-colors",
          accentColor.hover
        )}
      >
        <div className={clsx("shrink-0", accentColor.icon)}>
          {isSubAgent ? <Users size={18} /> : <CalendarClock size={18} />}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-gray-900 truncate">{agentName}</span>
            <CheckCircle2 size={14} className="text-green-600 shrink-0" />
          </div>
          <p className="text-xs text-gray-600 mt-0.5 truncate">{taskPrompt}</p>
        </div>
        <div className="shrink-0">
          {collapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
        </div>
      </button>

      {/* Metadata */}
      <div className={clsx("flex items-center gap-3 px-3.5 py-1.5 text-xs border-t", accentColor.border)}>
        {taskId && (
          <span className="text-gray-500 font-mono truncate max-w-[180px]" title={taskId}>
            ID: {taskId.slice(0, 8)}…
          </span>
        )}
        {jobName && !isSubAgent && (
          <span className="text-gray-500 truncate">{jobName}</span>
        )}
      </div>

      {/* Content */}
      {!collapsed && (
        <div
          className={clsx("px-3.5 py-2.5 text-sm text-gray-700 leading-relaxed whitespace-pre-wrap overflow-auto", accentColor.bg)}
          style={{ maxHeight: '400px' }}
        >
          {msg.content}
        </div>
      )}
    </div>
  );
}
