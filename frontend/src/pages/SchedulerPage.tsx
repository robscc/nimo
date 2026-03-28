import { RefreshCw, Loader2, StopCircle } from "lucide-react";
import clsx from "clsx";
import { useSchedulerAgents, useSchedulerStats, useStopAgent } from "../hooks/useScheduler";
import type { AgentProcessInfo, SchedulerStats } from "../api";

// ── 数字格式化 ──────────────────────────────────────────────

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toString();
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

// ── 状态颜色映射 ────────────────────────────────────────────

const STATE_COLORS: Record<string, string> = {
  running: "bg-emerald-100 text-emerald-700",
  idle: "bg-blue-100 text-blue-700",
  starting: "bg-amber-100 text-amber-700",
  pending: "bg-gray-100 text-gray-600",
  stopping: "bg-gray-200 text-gray-600",
  stopped: "bg-gray-100 text-gray-400",
  failed: "bg-red-100 text-red-700",
};

const TYPE_LABELS: Record<string, string> = {
  pa: "PA",
  sub_agent: "SubAgent",
  cron: "Cron",
};

const TYPE_COLORS: Record<string, string> = {
  pa: "bg-violet-100 text-violet-700",
  sub_agent: "bg-cyan-100 text-cyan-700",
  cron: "bg-amber-100 text-amber-700",
};

// ── 指标卡片 ────────────────────────────────────────────────

function StatCard({
  icon,
  label,
  value,
  gradient,
}: {
  icon: string;
  label: string;
  value: string | number;
  gradient: string;
}) {
  return (
    <div
      className={clsx(
        "relative overflow-hidden rounded-2xl p-5 text-white shadow-lg",
        gradient
      )}
    >
      <div className="absolute right-3 top-3 text-3xl opacity-20">{icon}</div>
      <p className="text-sm font-medium opacity-90">{label}</p>
      <p className="mt-1 text-3xl font-bold tracking-tight">
        {typeof value === "number" ? formatNumber(value) : value}
      </p>
    </div>
  );
}

// ── 状态分布条形图 ──────────────────────────────────────────

const BAR_COLORS = [
  "bg-emerald-500",
  "bg-blue-500",
  "bg-amber-500",
  "bg-violet-500",
  "bg-red-500",
  "bg-gray-400",
  "bg-cyan-500",
];

function StateDistribution({ byState }: { byState: Record<string, number> }) {
  const entries = Object.entries(byState).sort((a, b) => b[1] - a[1]);
  const maxVal = Math.max(...entries.map(([, v]) => v), 1);

  if (entries.length === 0) {
    return (
      <div className="rounded-2xl border border-gray-100 bg-white p-5 shadow-sm">
        <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700">
          <span>📊</span> 状态分布
        </h3>
        <p className="text-sm text-gray-400">暂无进程</p>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-gray-100 bg-white p-5 shadow-sm">
      <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700">
        <span>📊</span> 状态分布
      </h3>
      <div className="space-y-3">
        {entries.map(([name, count], i) => {
          const pct = (count / maxVal) * 100;
          const total = entries.reduce((s, [, v]) => s + v, 0);
          const share = total > 0 ? ((count / total) * 100).toFixed(0) : "0";
          return (
            <div key={name}>
              <div className="mb-1 flex items-center justify-between text-xs">
                <span className="font-medium text-gray-600">{name}</span>
                <span className="text-gray-400">
                  {count} <span className="text-gray-300">({share}%)</span>
                </span>
              </div>
              <div className="h-2.5 w-full overflow-hidden rounded-full bg-gray-100">
                <div
                  className={clsx(
                    "h-full rounded-full transition-all duration-500",
                    BAR_COLORS[i % BAR_COLORS.length]
                  )}
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── 进程列表表格 ────────────────────────────────────────────

function AgentTable({
  agents,
  onStop,
  stopping,
}: {
  agents: AgentProcessInfo[];
  onStop: (id: string) => void;
  stopping: boolean;
}) {
  if (agents.length === 0) {
    return (
      <div className="rounded-2xl border border-gray-100 bg-white p-8 text-center shadow-sm">
        <p className="text-sm text-gray-400">暂无活跃 Agent 进程</p>
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-gray-100 bg-white shadow-sm">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-100 bg-gray-50/60">
            <th className="px-4 py-3 text-left font-medium text-gray-500">类型</th>
            <th className="px-4 py-3 text-left font-medium text-gray-500">标识</th>
            <th className="px-4 py-3 text-left font-medium text-gray-500">Session / Task</th>
            <th className="px-4 py-3 text-left font-medium text-gray-500">状态</th>
            <th className="px-4 py-3 text-left font-medium text-gray-500">PID</th>
            <th className="px-4 py-3 text-left font-medium text-gray-500">空闲</th>
            <th className="px-4 py-3 text-left font-medium text-gray-500">操作</th>
          </tr>
        </thead>
        <tbody>
          {agents.map((agent) => (
            <tr
              key={agent.process_id}
              className="border-b border-gray-50 last:border-0 hover:bg-gray-50/50"
            >
              <td className="px-4 py-3">
                <span
                  className={clsx(
                    "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
                    TYPE_COLORS[agent.agent_type] || "bg-gray-100 text-gray-600"
                  )}
                >
                  {TYPE_LABELS[agent.agent_type] || agent.agent_type}
                </span>
              </td>
              <td className="px-4 py-3">
                <span className="font-mono text-xs text-gray-700">
                  {agent.process_id}
                </span>
              </td>
              <td className="px-4 py-3 text-xs text-gray-500">
                {agent.session_id && (
                  <span title={agent.session_id}>
                    {agent.session_id.length > 20
                      ? `${agent.session_id.slice(0, 20)}...`
                      : agent.session_id}
                  </span>
                )}
                {agent.task_id && (
                  <span title={agent.task_id}>
                    {agent.task_id.length > 20
                      ? `${agent.task_id.slice(0, 20)}...`
                      : agent.task_id}
                  </span>
                )}
                {!agent.session_id && !agent.task_id && <span className="text-gray-300">-</span>}
              </td>
              <td className="px-4 py-3">
                <span
                  className={clsx(
                    "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
                    STATE_COLORS[agent.state] || "bg-gray-100 text-gray-600"
                  )}
                >
                  {agent.state}
                </span>
              </td>
              <td className="px-4 py-3 font-mono text-xs text-gray-500">
                {agent.os_pid ?? "-"}
              </td>
              <td className="px-4 py-3 text-xs text-gray-500">
                {formatDuration(agent.idle_seconds)}
              </td>
              <td className="px-4 py-3">
                {agent.state !== "stopped" && agent.state !== "failed" && (
                  <button
                    onClick={() => onStop(agent.process_id)}
                    disabled={stopping}
                    className="flex items-center gap-1 rounded-lg border border-gray-200 px-2 py-1 text-xs font-medium text-gray-500 transition-colors hover:border-red-300 hover:text-red-600"
                    title="停止进程"
                  >
                    <StopCircle size={12} />
                    停止
                  </button>
                )}
                {agent.error && (
                  <span className="mt-1 block text-xs text-red-500" title={agent.error}>
                    {agent.error.length > 30
                      ? `${agent.error.slice(0, 30)}...`
                      : agent.error}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── 主页面 ──────────────────────────────────────────────────

export default function SchedulerPage() {
  const {
    data: agents,
    isLoading: agentsLoading,
    isFetching: agentsFetching,
    refetch: refetchAgents,
    dataUpdatedAt,
  } = useSchedulerAgents();

  const { data: stats } = useSchedulerStats();

  const stopMutation = useStopAgent();

  const lastUpdated = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString("zh-CN")
    : "--:--:--";

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl px-6 py-8">
        {/* Header */}
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-gray-800">
              <span className="mr-2">🖥️</span>调度器
            </h1>
            <p className="mt-1 text-sm text-gray-400">
              Agent 进程管理与监控
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-gray-400">
              更新于 {lastUpdated}
            </span>
            <button
              onClick={() => refetchAgents()}
              disabled={agentsFetching}
              className={clsx(
                "flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-50",
                agentsFetching && "opacity-50"
              )}
            >
              <RefreshCw
                size={14}
                className={clsx(agentsFetching && "animate-spin")}
              />
              刷新
            </button>
          </div>
        </div>

        {/* Loading */}
        {agentsLoading && (
          <div className="flex h-64 items-center justify-center">
            <Loader2 size={32} className="animate-spin text-gray-300" />
          </div>
        )}

        {/* Content */}
        {stats && (
          <div className="space-y-6">
            {/* Top 指标卡片 */}
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <StatCard
                icon="📊"
                label="总进程"
                value={stats.total_processes}
                gradient="bg-gradient-to-br from-violet-500 to-blue-600"
              />
              <StatCard
                icon="🧠"
                label="PA 进程"
                value={stats.pa_count}
                gradient="bg-gradient-to-br from-blue-500 to-cyan-500"
              />
              <StatCard
                icon="🤖"
                label="SubAgent"
                value={stats.sub_agent_count}
                gradient="bg-gradient-to-br from-cyan-500 to-emerald-500"
              />
              <StatCard
                icon="⏰"
                label="运行时间"
                value={formatDuration(stats.uptime_seconds)}
                gradient="bg-gradient-to-br from-orange-400 to-amber-500"
              />
            </div>

            {/* 状态分布 */}
            <StateDistribution byState={stats.by_state} />

            {/* 进程列表 */}
            <div>
              <h2 className="mb-3 text-sm font-semibold text-gray-700">
                活跃进程
              </h2>
              <AgentTable
                agents={agents || []}
                onStop={(id) => stopMutation.mutate(id)}
                stopping={stopMutation.isPending}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
