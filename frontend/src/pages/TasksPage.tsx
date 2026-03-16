import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bot, CheckCircle2, XCircle, Timer, Loader2,
  ChevronDown, ChevronRight, RefreshCw, ClipboardList,
  AlertCircle, Hash,
} from "lucide-react";
import clsx from "clsx";
import { listAllSubAgentTasks, type TaskListItem } from "../api";

// ── helpers ───────────────────────────────────────────────

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s} 秒前`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  return new Date(iso).toLocaleDateString("zh-CN");
}

function duration(createdAt: string, finishedAt: string | null): string | null {
  if (!finishedAt) return null;
  const ms = new Date(finishedAt).getTime() - new Date(createdAt).getTime();
  if (ms < 1000) return `${ms}ms`;
  const s = (ms / 1000).toFixed(1);
  return `${s}s`;
}

// ── status config ─────────────────────────────────────────

const STATUS_CFG = {
  done:      { icon: CheckCircle2, color: "text-green-500",  bg: "bg-green-50",  border: "border-green-200", label: "已完成" },
  failed:    { icon: XCircle,      color: "text-red-500",    bg: "bg-red-50",    border: "border-red-200",   label: "失败"   },
  running:   { icon: Loader2,      color: "text-amber-500",  bg: "bg-amber-50",  border: "border-amber-200", label: "执行中", spin: true },
  pending:   { icon: Timer,        color: "text-gray-400",   bg: "bg-gray-50",   border: "border-gray-200",  label: "等待中" },
  cancelled: { icon: AlertCircle,  color: "text-gray-400",   bg: "bg-gray-50",   border: "border-gray-200",  label: "已取消" },
} as const;

// ── filter tabs ───────────────────────────────────────────

const TABS = [
  { key: "",          label: "全部"   },
  { key: "running",   label: "执行中" },
  { key: "pending",   label: "等待中" },
  { key: "done",      label: "已完成" },
  { key: "failed",    label: "失败"   },
] as const;

// ── TaskCard ──────────────────────────────────────────────

function TaskCard({ task }: { task: TaskListItem }) {
  const [expanded, setExpanded] = useState(false);
  const cfg = STATUS_CFG[task.status as keyof typeof STATUS_CFG] ?? STATUS_CFG.pending;
  const Icon = cfg.icon;
  const dur = duration(task.created_at, task.finished_at);
  const hasDetail = !!(task.result || task.error);

  return (
    <div className={clsx("rounded-xl border bg-white transition-shadow", cfg.border, expanded ? "shadow-sm" : "hover:shadow-sm")}>
      {/* Main row */}
      <div className="flex items-start gap-3 px-4 py-3">
        {/* Status icon */}
        <div className={clsx("mt-0.5 shrink-0", cfg.color)}>
          <Icon size={16} className={"spin" in cfg ? "animate-spin" : undefined} />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          {/* Badges row */}
          <div className="flex items-center gap-1.5 flex-wrap mb-1.5">
            {task.agent_name && (
              <span className="inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full bg-nimo-100 text-nimo-700">
                <Bot size={9} />
                {task.agent_name}
              </span>
            )}
            {task.task_type && (
              <span className="text-[11px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 font-mono">
                {task.task_type}
              </span>
            )}
            <span className={clsx("text-[11px] px-2 py-0.5 rounded-full font-medium", cfg.bg, cfg.color)}>
              {cfg.label}
            </span>
            <span className="ml-auto text-[11px] text-gray-400 shrink-0">
              {relativeTime(task.created_at)}
              {dur && <span className="ml-1 text-gray-300">· {dur}</span>}
            </span>
          </div>

          {/* Task prompt */}
          <p className="text-sm text-gray-700 leading-relaxed">
            {task.task_prompt}
          </p>

          {/* Session ID */}
          <p className="text-[11px] text-gray-400 mt-1.5 font-mono truncate">
            <span className="text-gray-300">session: </span>
            {task.parent_session_id}
          </p>
        </div>

        {/* Expand toggle */}
        {hasDetail && (
          <button
            onClick={() => setExpanded(v => !v)}
            className="mt-0.5 p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100 shrink-0 transition-colors"
            title={expanded ? "收起" : "展开结果"}
          >
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        )}
      </div>

      {/* Expanded result/error */}
      {expanded && hasDetail && (
        <div className="px-4 pb-3 border-t mx-4 mt-0 pt-3">
          {task.result && (
            <div>
              <p className="text-[10px] font-medium text-gray-400 uppercase tracking-wider mb-1.5">执行结果</p>
              <pre className="text-xs text-gray-700 bg-gray-50 rounded-lg p-3 overflow-auto max-h-64 whitespace-pre-wrap leading-relaxed">
                {task.result}
              </pre>
            </div>
          )}
          {task.error && (
            <div className={task.result ? "mt-3" : ""}>
              <p className="text-[10px] font-medium text-red-400 uppercase tracking-wider mb-1.5">错误信息</p>
              <pre className="text-xs text-red-600 bg-red-50 rounded-lg p-3 overflow-auto max-h-40 whitespace-pre-wrap">
                {task.error}
              </pre>
            </div>
          )}
          <p className="text-[10px] text-gray-300 font-mono mt-2">ID: {task.task_id}</p>
        </div>
      )}
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────

export default function TasksPage() {
  const [activeTab, setActiveTab] = useState<string>("");

  const { data: tasks = [], isLoading, isFetching, refetch } = useQuery({
    queryKey: ["sub-agent-tasks", activeTab],
    queryFn: () => listAllSubAgentTasks(activeTab || undefined),
    staleTime: 0,           // 每次进入页面都重新拉取，不用缓存
    refetchOnMount: true,
    refetchInterval: (query) => {
      const d = query.state.data as TaskListItem[] | undefined;
      const hasActive = d?.some(t => t.status === "running" || t.status === "pending");
      return hasActive ? 3000 : false;
    },
  });

  // Tab counts
  const counts = tasks.reduce<Record<string, number>>((acc, t) => {
    acc[t.status] = (acc[t.status] ?? 0) + 1;
    return acc;
  }, {});

  const displayed = activeTab
    ? tasks.filter(t => t.status === activeTab)
    : tasks;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 bg-white border-b">
        <div className="flex items-center justify-between mb-3">
          <h1 className="font-semibold text-gray-800 flex items-center gap-2">
            <ClipboardList size={18} className="text-nimo-500" />
            SubAgent 任务历史
          </h1>
          <div className="flex items-center gap-3">
            <span className="text-xs text-gray-400 flex items-center gap-1">
              <Hash size={11} /> {tasks.length} 条记录
            </span>
            <button
              onClick={() => refetch()}
              className={clsx(
                "p-1.5 rounded-lg text-gray-400 hover:text-nimo-600 hover:bg-nimo-50 transition-colors",
                isFetching && "animate-spin text-nimo-400"
              )}
              title="刷新"
            >
              <RefreshCw size={14} />
            </button>
          </div>
        </div>

        {/* Filter tabs */}
        <div className="flex gap-1">
          {TABS.map(tab => {
            const count = tab.key
              ? (counts[tab.key] ?? 0)
              : tasks.length;
            const isActive = activeTab === tab.key;
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={clsx(
                  "px-3 py-1.5 rounded-lg text-xs font-medium transition-colors flex items-center gap-1.5",
                  isActive
                    ? "bg-nimo-100 text-nimo-700"
                    : "text-gray-500 hover:bg-gray-100"
                )}
              >
                {tab.label}
                {count > 0 && (
                  <span className={clsx(
                    "text-[10px] px-1.5 py-0.5 rounded-full",
                    isActive ? "bg-nimo-200 text-nimo-700" : "bg-gray-100 text-gray-400"
                  )}>
                    {count}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {isLoading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 size={24} className="animate-spin text-gray-300" />
          </div>
        ) : displayed.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-gray-400 gap-3">
            <Bot size={40} className="text-gray-200" />
            <p className="text-sm">
              {activeTab ? `暂无「${TABS.find(t => t.key === activeTab)?.label}」任务` : "暂无任务记录"}
            </p>
            <p className="text-xs text-gray-300">在对话中使用 dispatch_sub_agent 工具后任务将显示在这里</p>
          </div>
        ) : (
          <div className="space-y-2">
            {displayed.map(task => (
              <TaskCard key={task.task_id} task={task} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
