import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  MessageSquare, Trash2, Search, Cpu, Hash,
  Loader2, MessagesSquare, ChevronDown, ChevronRight, ExternalLink,
  Bot, CheckCircle2, XCircle, AlertCircle, Timer, Square,
} from "lucide-react";
import clsx from "clsx";
import { useAllSessions } from "../hooks/useSessions";
import { deleteSession, getSessionSubTasks, cancelTask, type SessionSummary, type SubTaskSummary } from "../api";

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const min = Math.floor(diff / 60000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day} 天前`;
  return new Date(iso).toLocaleDateString("zh-CN");
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// ── SubTask helpers ────────────────────────────────────────

const STATUS_CONFIG = {
  done:      { icon: CheckCircle2, color: "text-green-500",  bg: "bg-green-50",  label: "完成"   },
  failed:    { icon: XCircle,      color: "text-red-500",    bg: "bg-red-50",    label: "失败"   },
  running:   { icon: Loader2,      color: "text-amber-500",  bg: "bg-amber-50",  label: "执行中", spin: true },
  pending:   { icon: Timer,        color: "text-gray-400",   bg: "bg-gray-50",   label: "等待中" },
  cancelled: { icon: AlertCircle,  color: "text-gray-400",   bg: "bg-gray-50",   label: "已取消" },
} as const;

function SubTaskItem({ task, onCancel }: { task: SubTaskSummary; onCancel?: (taskId: string) => void }) {
  const cfg = STATUS_CONFIG[task.status as keyof typeof STATUS_CONFIG] ?? STATUS_CONFIG.pending;
  const Icon = cfg.icon;
  const isCancellable = task.status === "running" || task.status === "pending" || task.status === "input_required";

  return (
    <div className="flex items-start gap-2.5 px-3 py-2 rounded-lg bg-white border border-gray-100 hover:border-gray-200 transition-colors">
      {/* Status icon */}
      <div className={clsx("mt-0.5 shrink-0", cfg.color)}>
        <Icon size={13} className={"spin" in cfg ? "animate-spin" : undefined} />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 flex-wrap">
          {/* Agent badge */}
          {task.agent_name && (
            <span className="inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded bg-nimo-100 text-nimo-700">
              <Bot size={9} />
              {task.agent_name}
            </span>
          )}
          {task.task_type && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 font-mono">
              {task.task_type}
            </span>
          )}
          {/* Status badge */}
          <span className={clsx("text-[10px] px-1.5 py-0.5 rounded font-medium", cfg.bg, cfg.color)}>
            {cfg.label}
          </span>
          {/* Time */}
          <span className="text-[10px] text-gray-400 ml-auto shrink-0">
            {relativeTime(task.created_at)}
          </span>
        </div>
        {/* Prompt */}
        <p className="text-xs text-gray-600 mt-1 leading-relaxed line-clamp-2">
          {task.task_prompt}
        </p>
      </div>

      {/* Cancel button */}
      {isCancellable && onCancel && (
        <button
          onClick={() => onCancel(task.id)}
          className="ml-1 p-1 rounded text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors"
          title="取消任务"
        >
          <Square size={12} />
        </button>
      )}
    </div>
  );
}

function SubTaskList({ sessionId, onCancel }: { sessionId: string; onCancel?: (taskId: string) => void }) {
  const { data: tasks = [], isLoading } = useQuery({
    queryKey: ["session-sub-tasks", sessionId],
    queryFn: () => getSessionSubTasks(sessionId),
    staleTime: 15_000,
  });

  if (isLoading) {
    return (
      <div className="flex items-center gap-1.5 py-2 text-xs text-gray-400">
        <Loader2 size={12} className="animate-spin" /> 加载子任务…
      </div>
    );
  }

  if (tasks.length === 0) {
    return <p className="text-xs text-gray-400 py-1">暂无子任务记录</p>;
  }

  return (
    <div className="space-y-1.5">
      {tasks.map((t) => (
        <SubTaskItem key={t.id} task={t} onCancel={onCancel} />
      ))}
    </div>
  );
}

// ── SessionRow ─────────────────────────────────────────────

function SessionRow({
  session,
  onOpen,
  onDelete,
  deleting,
}: {
  session: SessionSummary;
  onOpen: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const queryClient = useQueryClient();
  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const isDingtalk = session.channel === "dingtalk";
  const hasSubTasks = session.sub_tasks_count > 0;

  const handleCancelTask = async (taskId: string) => {
    if (!confirm("确定要取消此任务吗？")) return;
    setCancellingId(taskId);
    try {
      await cancelTask(taskId);
      // Refresh session list and sub-tasks
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
      queryClient.invalidateQueries({ queryKey: ["session-sub-tasks", session.id] });
    } catch (err) {
      console.error("Failed to cancel task:", err);
      alert("取消任务失败，请重试");
    } finally {
      setCancellingId(null);
    }
  };

  return (
    <div className={clsx(
      "border rounded-xl bg-white transition-shadow",
      expanded ? "shadow-sm ring-1 ring-nimo-100" : "hover:shadow-sm"
    )}>
      {/* Main row */}
      <div className="flex items-center gap-3 px-4 py-3">
        {/* Expand toggle */}
        <button
          onClick={() => setExpanded((v) => !v)}
          className="w-6 h-6 flex items-center justify-center rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100 shrink-0"
        >
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>

        {/* Icon */}
        <div className="w-9 h-9 rounded-lg bg-nimo-50 flex items-center justify-center shrink-0">
          <MessageSquare size={16} className="text-nimo-500" />
        </div>

        {/* Title + time */}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-800 truncate">
            {session.title}
            {isDingtalk && (
              <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-100 text-blue-600">
                钉钉
              </span>
            )}
          </p>
          <p className="text-xs text-gray-400 mt-0.5">{relativeTime(session.updated_at)}</p>
        </div>

        {/* Sub-tasks badge */}
        {hasSubTasks && (
          <span
            className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-nimo-50 text-nimo-600 shrink-0 cursor-pointer"
            onClick={() => setExpanded(true)}
            title={`${session.sub_tasks_count} 个 SubAgent 子任务`}
          >
            <Bot size={10} />
            {session.sub_tasks_count}
          </span>
        )}

        {/* Model badge */}
        <span className="hidden sm:inline-flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 shrink-0">
          <Cpu size={10} />
          {session.model_name || "default"}
        </span>

        {/* Message count */}
        <span className="inline-flex items-center gap-1 text-xs text-gray-400 shrink-0 w-16 justify-end">
          <Hash size={11} />
          {session.message_count}
        </span>

        {/* Actions */}
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={onOpen}
            className="p-1.5 rounded-lg text-gray-400 hover:text-nimo-600 hover:bg-nimo-50 transition-colors"
            title="打开对话"
          >
            <ExternalLink size={14} />
          </button>
          {!isDingtalk && (
            <button
              onClick={onDelete}
              disabled={deleting}
              className="p-1.5 rounded-lg text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors disabled:opacity-40"
              title="删除会话"
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="px-4 pb-4 pt-1 border-t mx-4 mt-0">
          {/* Session meta grid */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3">
            <div className="space-y-0.5">
              <p className="text-[10px] font-medium text-gray-400 uppercase tracking-wider">模型</p>
              <p className="text-xs font-mono text-gray-700">{session.model_name || "默认"}</p>
            </div>
            <div className="space-y-0.5">
              <p className="text-[10px] font-medium text-gray-400 uppercase tracking-wider">渠道</p>
              <p className="text-xs text-gray-700">{session.channel}</p>
            </div>
            <div className="space-y-0.5">
              <p className="text-[10px] font-medium text-gray-400 uppercase tracking-wider">创建时间</p>
              <p className="text-xs text-gray-700">{formatDate(session.created_at)}</p>
            </div>
            <div className="space-y-0.5">
              <p className="text-[10px] font-medium text-gray-400 uppercase tracking-wider">最后活跃</p>
              <p className="text-xs text-gray-700">{formatDate(session.updated_at)}</p>
            </div>
          </div>
          <div className="mt-3">
            <p className="text-[10px] font-medium text-gray-400 uppercase tracking-wider">Session ID</p>
            <p className="text-xs font-mono text-gray-500 mt-0.5 select-all">{session.id}</p>
          </div>

          {/* Sub-tasks section */}
          <div className="mt-4">
            <div className="flex items-center gap-1.5 mb-2">
              <Bot size={12} className="text-nimo-500" />
              <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider">
                SubAgent 子任务
                {hasSubTasks && (
                  <span className="ml-1.5 text-nimo-500 normal-case font-normal">
                    ({session.sub_tasks_count})
                  </span>
                )}
              </p>
            </div>
            <SubTaskList sessionId={session.id} onCancel={handleCancelTask} />
          </div>
        </div>
      )}
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────

export default function SessionsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: sessions = [], isLoading } = useAllSessions();
  const [search, setSearch] = useState("");
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const filtered = sessions.filter((s) =>
    !search || s.title.toLowerCase().includes(search.toLowerCase()) || s.id.includes(search)
  );

  const handleOpen = (id: string) => navigate(`/chat?session=${id}`);

  const handleDelete = async (id: string) => {
    setDeletingId(id);
    try {
      await deleteSession(id);
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
    } finally {
      setDeletingId(null);
    }
  };

  const totalMessages = sessions.reduce((sum, s) => sum + s.message_count, 0);
  const totalSubTasks = sessions.reduce((sum, s) => sum + s.sub_tasks_count, 0);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 bg-white border-b">
        <div className="flex items-center justify-between mb-3">
          <h1 className="font-semibold text-gray-800 flex items-center gap-2">
            <MessagesSquare size={18} className="text-nimo-500" />
            会话管理
          </h1>
          <div className="flex items-center gap-3 text-xs text-gray-400">
            <span className="flex items-center gap-1">
              <MessageSquare size={12} /> {sessions.length} 个会话
            </span>
            <span className="flex items-center gap-1">
              <Hash size={12} /> {totalMessages} 条消息
            </span>
            {totalSubTasks > 0 && (
              <span className="flex items-center gap-1 text-nimo-500">
                <Bot size={12} /> {totalSubTasks} 个子任务
              </span>
            )}
          </div>
        </div>

        {/* Search */}
        <div className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索会话标题或 ID ..."
            className="w-full pl-9 pr-4 py-2 rounded-lg border border-gray-200 text-sm outline-none focus:border-nimo-400 transition-colors bg-gray-50"
          />
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {isLoading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 size={24} className="animate-spin text-gray-300" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-gray-400 gap-3">
            <MessagesSquare size={40} className="text-gray-200" />
            <p className="text-sm">{search ? "没有匹配的会话" : "暂无会话"}</p>
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map((session) => (
              <SessionRow
                key={session.id}
                session={session}
                onOpen={() => handleOpen(session.id)}
                onDelete={() => handleDelete(session.id)}
                deleting={deletingId === session.id}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
