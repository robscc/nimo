import { useState } from "react";
import {
  CheckCircle,
  Clock,
  XCircle,
  Loader,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  Filter,
} from "lucide-react";
import { useTasks } from "../hooks/useTasks";
import type { TaskStatusResponse, TaskListParams } from "../api";

const STATUS_CONFIG: Record<
  string,
  { icon: typeof Clock; color: string; bg: string; label: string }
> = {
  pending: { icon: Clock, color: "text-yellow-600", bg: "bg-yellow-50", label: "等待中" },
  running: { icon: Loader, color: "text-blue-600", bg: "bg-blue-50", label: "执行中" },
  done: { icon: CheckCircle, color: "text-green-600", bg: "bg-green-50", label: "已完成" },
  failed: { icon: XCircle, color: "text-red-600", bg: "bg-red-50", label: "失败" },
  cancelled: { icon: XCircle, color: "text-gray-500", bg: "bg-gray-50", label: "已取消" },
};

const STATUS_OPTIONS = [
  { value: "", label: "全部状态" },
  { value: "pending", label: "等待中" },
  { value: "running", label: "执行中" },
  { value: "done", label: "已完成" },
  { value: "failed", label: "失败" },
  { value: "cancelled", label: "已取消" },
];

function PriorityBadge({ priority }: { priority: number }) {
  const colors: Record<number, string> = {
    1: "bg-gray-100 text-gray-600",
    2: "bg-gray-100 text-gray-600",
    3: "bg-blue-100 text-blue-700",
    4: "bg-blue-100 text-blue-700",
    5: "bg-indigo-100 text-indigo-700",
    6: "bg-indigo-100 text-indigo-700",
    7: "bg-orange-100 text-orange-700",
    8: "bg-orange-100 text-orange-700",
    9: "bg-red-100 text-red-700",
    10: "bg-red-100 text-red-700",
  };
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${colors[priority] ?? "bg-gray-100 text-gray-600"}`}
    >
      P{priority}
    </span>
  );
}

function RetryBadge({ retryCount, maxRetries }: { retryCount: number; maxRetries: number }) {
  if (retryCount === 0 && maxRetries === 0) return null;
  const isRetrying = retryCount > 0;
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${
        isRetrying ? "bg-amber-100 text-amber-700" : "bg-gray-100 text-gray-500"
      }`}
    >
      <RefreshCw size={10} className={isRetrying ? "animate-spin" : ""} />
      {retryCount}/{maxRetries}
    </span>
  );
}

function TaskCard({ task }: { task: TaskStatusResponse }) {
  const cfg = STATUS_CONFIG[task.status] ?? STATUS_CONFIG.pending;
  const Icon = cfg.icon;

  return (
    <div className={`p-4 bg-white rounded-xl border hover:shadow-sm transition-shadow`}>
      <div className="flex items-center gap-2 mb-2">
        <div className={`p-1 rounded ${cfg.bg}`}>
          <Icon
            size={14}
            className={`${cfg.color} ${task.status === "running" ? "animate-spin" : ""}`}
          />
        </div>
        <span className={`text-sm font-medium ${cfg.color}`}>{cfg.label}</span>
        <PriorityBadge priority={task.priority} />
        <RetryBadge retryCount={task.retry_count} maxRetries={task.max_retries} />
        <span className="text-xs text-gray-400 ml-auto font-mono">{task.task_id.slice(0, 8)}</span>
      </div>

      <div className="flex items-center gap-2 text-xs text-gray-500 mb-2">
        {task.agent_name && (
          <span className="bg-violet-50 text-violet-600 px-1.5 py-0.5 rounded">
            {task.agent_name}
          </span>
        )}
        {task.task_type && (
          <span className="bg-sky-50 text-sky-600 px-1.5 py-0.5 rounded">{task.task_type}</span>
        )}
        {task.created_at && (
          <span className="ml-auto">
            {new Date(task.created_at).toLocaleString("zh-CN", {
              month: "2-digit",
              day: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </span>
        )}
      </div>

      {task.result && (
        <p className="text-sm text-gray-700 line-clamp-2 bg-gray-50 rounded p-2">{task.result}</p>
      )}
      {task.error && (
        <p className="text-sm text-red-600 line-clamp-2 bg-red-50 rounded p-2">{task.error}</p>
      )}
    </div>
  );
}

const PAGE_SIZE = 20;

export default function TasksPage() {
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(0);

  const params: TaskListParams = {
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
    ...(statusFilter ? { status: statusFilter } : {}),
  };

  const { data, isLoading } = useTasks(params);
  const tasks = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-semibold text-gray-800 text-lg">SubAgent 任务</h1>
        <div className="flex items-center gap-2">
          <Filter size={14} className="text-gray-400" />
          <select
            className="text-sm border rounded-lg px-3 py-1.5 bg-white text-gray-700 focus:outline-none focus:ring-2 focus:ring-indigo-300"
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              setPage(0);
            }}
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-20 text-gray-400">
          <Loader size={20} className="animate-spin mr-2" />
          加载中...
        </div>
      ) : tasks.length === 0 ? (
        <div className="text-center py-20">
          <p className="text-gray-400 text-sm">
            {statusFilter ? "没有符合筛选条件的任务" : "暂无任务。在对话中触发 SubAgent 后任务将显示在这里。"}
          </p>
        </div>
      ) : (
        <>
          <div className="space-y-3">
            {tasks.map((task) => (
              <TaskCard key={task.task_id} task={task} />
            ))}
          </div>

          {/* 分页 */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between mt-6 text-sm text-gray-500">
              <span>
                共 {total} 条，第 {page + 1}/{totalPages} 页
              </span>
              <div className="flex items-center gap-2">
                <button
                  className="p-1 rounded hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
                  disabled={page === 0}
                  onClick={() => setPage((p) => p - 1)}
                >
                  <ChevronLeft size={16} />
                </button>
                <button
                  className="p-1 rounded hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
                  disabled={page >= totalPages - 1}
                  onClick={() => setPage((p) => p + 1)}
                >
                  <ChevronRight size={16} />
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
