import { useQuery } from "@tanstack/react-query";
import { CheckCircle, Clock, XCircle, Loader } from "lucide-react";
import { getTaskStatus, type TaskStatusResponse } from "../api";
import { useState } from "react";

const STATUS_CONFIG = {
  pending: { icon: Clock, color: "text-yellow-500", label: "等待中" },
  running: { icon: Loader, color: "text-blue-500", label: "执行中" },
  done: { icon: CheckCircle, color: "text-green-500", label: "已完成" },
  failed: { icon: XCircle, color: "text-red-500", label: "失败" },
  cancelled: { icon: XCircle, color: "text-gray-400", label: "已取消" },
};

function TaskCard({ taskId }: { taskId: string }) {
  const { data, isLoading } = useQuery<TaskStatusResponse>({
    queryKey: ["task", taskId],
    queryFn: () => getTaskStatus(taskId),
    refetchInterval: (data) =>
      data?.status === "pending" || data?.status === "running" ? 2000 : false,
  });

  if (isLoading || !data) return <div className="p-4 bg-white rounded-xl border animate-pulse h-20" />;

  const cfg = STATUS_CONFIG[data.status] ?? STATUS_CONFIG.pending;
  const Icon = cfg.icon;

  return (
    <div className="p-4 bg-white rounded-xl border">
      <div className="flex items-center gap-2 mb-2">
        <Icon size={16} className={cfg.color} />
        <span className={`text-sm font-medium ${cfg.color}`}>{cfg.label}</span>
        <span className="text-xs text-gray-400 ml-auto font-mono">{taskId.slice(0, 8)}</span>
      </div>
      {data.result && <p className="text-sm text-gray-700">{data.result}</p>}
      {data.error && <p className="text-sm text-red-500">{data.error}</p>}
    </div>
  );
}

export default function TasksPage() {
  const [taskIds] = useState<string[]>([]);

  return (
    <div className="p-6">
      <h1 className="font-semibold text-gray-800 mb-4">SubAgent 任务</h1>
      {taskIds.length === 0 ? (
        <p className="text-gray-400 text-sm">暂无任务。在对话中触发 SubAgent 后任务将显示在这里。</p>
      ) : (
        <div className="space-y-3">
          {taskIds.map((id) => (
            <TaskCard key={id} taskId={id} />
          ))}
        </div>
      )}
    </div>
  );
}
