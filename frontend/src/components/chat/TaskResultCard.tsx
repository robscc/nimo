import { useState } from "react";
import clsx from "clsx";
import ReactMarkdown from "react-markdown";
import type { Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import { CheckCircle2, ChevronDown, ChevronRight, Users, CalendarClock } from "lucide-react";
import type { Message } from "../../types/chat";

const markdownPlugins = [remarkGfm, remarkBreaks];
const markdownRehypePlugins = [rehypeRaw, rehypeSanitize];

const markdownComponents: Components = {
  p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
  code: ({ children, className, ...props }) => (
    <code
      className={clsx(
        "text-[0.92em]",
        !className?.includes("language-") && "rounded bg-gray-100 px-1 py-0.5",
        className,
      )}
      {...props}
    >
      {children}
    </code>
  ),
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-nimo-600 underline break-all">
      {children}
    </a>
  ),
  ul: ({ children }) => <ul className="list-disc pl-5 my-2">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal pl-5 my-2">{children}</ol>,
};

export default function TaskResultCard({ msg }: { msg: Message }) {
  const [collapsed, setCollapsed] = useState(false);
  const isSubAgent = msg.cardType === "sub_agent_result";
  const isCron = msg.cardType === "cron_result";

  const agentName = (msg.cardMeta?.agent_name as string) || (isSubAgent ? "SubAgent" : "Cron");
  const taskId = (msg.cardMeta?.task_id as string) || "";
  const jobName = (msg.cardMeta?.job_name as string) || "";

  const title = isSubAgent
    ? `SubAgent「${agentName}」任务完成`
    : `定时任务「${jobName}」执行完成`;

  const accentColor = isSubAgent
    ? { border: "border-indigo-200", bg: "bg-gradient-to-br from-indigo-50 to-white", icon: "text-indigo-500", badge: "bg-indigo-100 text-indigo-700", headerBg: "bg-indigo-500" }
    : { border: "border-amber-200", bg: "bg-gradient-to-br from-amber-50 to-white", icon: "text-amber-500", badge: "bg-amber-100 text-amber-700", headerBg: "bg-amber-500" };

  const Icon = isSubAgent ? Users : CalendarClock;

  return (
    <div className={clsx("rounded-xl border shadow-sm overflow-hidden max-w-[85%]", accentColor.border)}>
      {/* Header */}
      <div className={clsx("flex items-center gap-2 px-3.5 py-2", accentColor.headerBg)}>
        <Icon size={15} className="text-white shrink-0" />
        <span className="text-xs font-semibold text-white truncate">{title}</span>
        <div className="ml-auto flex items-center gap-1.5">
          <CheckCircle2 size={13} className="text-white/80" />
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="text-white/70 hover:text-white transition-colors"
          >
            {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
          </button>
        </div>
      </div>

      {/* Meta badges */}
      <div className={clsx("flex items-center gap-2 px-3.5 py-1.5 border-b text-[11px]", accentColor.border, accentColor.bg)}>
        <span className={clsx("px-1.5 py-0.5 rounded font-medium", accentColor.badge)}>
          {isSubAgent ? agentName : "cron"}
        </span>
        {taskId && (
          <span className="text-gray-400 font-mono truncate max-w-[180px]" title={taskId}>
            ID: {taskId.slice(0, 8)}…
          </span>
        )}
        {jobName && !isSubAgent && (
          <span className="text-gray-500 truncate">{jobName}</span>
        )}
      </div>

      {/* Content */}
      {!collapsed && (
        <div className={clsx("px-3.5 py-2.5 text-sm text-gray-700 leading-relaxed break-words max-h-64 overflow-y-auto", accentColor.bg)}>
          <ReactMarkdown
            remarkPlugins={markdownPlugins}
            rehypePlugins={markdownRehypePlugins}
            components={markdownComponents}
          >
            {msg.content}
          </ReactMarkdown>
        </div>
      )}
    </div>
  );
}
