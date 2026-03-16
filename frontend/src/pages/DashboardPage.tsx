import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw, Loader2, RotateCcw } from "lucide-react";
import clsx from "clsx";
import { getDashboardStats, reloadServiceConfig, type DashboardStats } from "../api";

// ── 数字格式化 ──────────────────────────────────────────────

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toString();
}

// ── 颜色配置 ─────────────────────────────────────────────────

const BAR_COLORS = [
  "bg-violet-500",
  "bg-blue-500",
  "bg-cyan-500",
  "bg-emerald-500",
  "bg-amber-500",
  "bg-rose-500",
  "bg-indigo-500",
  "bg-teal-500",
  "bg-orange-500",
  "bg-pink-500",
];

// ── 指标卡片组件 ──────────────────────────────────────────────

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

// ── 分布条形图组件 ────────────────────────────────────────────

function DistributionCard({
  title,
  icon,
  data,
}: {
  title: string;
  icon: string;
  data: Record<string, number>;
}) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const maxVal = Math.max(...entries.map(([, v]) => v), 1);

  if (entries.length === 0) {
    return (
      <div className="rounded-2xl border border-gray-100 bg-white p-5 shadow-sm">
        <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700">
          <span>{icon}</span> {title}
        </h3>
        <p className="text-sm text-gray-400">暂无数据</p>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-gray-100 bg-white p-5 shadow-sm">
      <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700">
        <span>{icon}</span> {title}
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

// ── 工具排行榜组件 ────────────────────────────────────────────

function ToolRankingCard({
  data,
}: {
  data: Record<string, number>;
}) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const maxVal = Math.max(...entries.map(([, v]) => v), 1);

  if (entries.length === 0) {
    return (
      <div className="rounded-2xl border border-gray-100 bg-white p-5 shadow-sm">
        <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700">
          <span>🏆</span> 工具调用 Top 10
        </h3>
        <p className="text-sm text-gray-400">暂无数据</p>
      </div>
    );
  }

  const rankGradients = [
    "from-amber-400 to-yellow-500",
    "from-gray-300 to-gray-400",
    "from-amber-600 to-amber-700",
  ];

  return (
    <div className="rounded-2xl border border-gray-100 bg-white p-5 shadow-sm">
      <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700">
        <span>🏆</span> 工具调用 Top 10
      </h3>
      <div className="space-y-2.5">
        {entries.map(([name, count], i) => {
          const pct = (count / maxVal) * 100;
          return (
            <div key={name} className="flex items-center gap-3">
              <span
                className={clsx(
                  "flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-xs font-bold",
                  i < 3
                    ? `bg-gradient-to-br ${rankGradients[i]} text-white`
                    : "bg-gray-100 text-gray-500"
                )}
              >
                {i + 1}
              </span>
              <div className="flex-1">
                <div className="mb-0.5 flex items-center justify-between text-xs">
                  <span className="font-mono font-medium text-gray-700">
                    {name}
                  </span>
                  <span className="text-gray-400">{count}</span>
                </div>
                <div className="h-2 w-full overflow-hidden rounded-full bg-gray-100">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-violet-500 to-purple-500 transition-all duration-500"
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── 系统状态面板组件 ──────────────────────────────────────────

function SystemStatusCard({ stats }: { stats: DashboardStats }) {
  const [reloading, setReloading] = useState(false);
  const [reloadResult, setReloadResult] = useState<string | null>(null);

  const handleReloadConfig = async () => {
    setReloading(true);
    setReloadResult(null);
    try {
      const res = await reloadServiceConfig();
      setReloadResult(`${res.llm_provider} / ${res.llm_model}`);
      setTimeout(() => setReloadResult(null), 4000);
    } catch {
      setReloadResult("重载失败");
      setTimeout(() => setReloadResult(null), 4000);
    } finally {
      setReloading(false);
    }
  };

  const items = [
    {
      icon: "🧩",
      label: "技能",
      detail: `${stats.total_skills} 已安装 / ${stats.enabled_skills} 启用`,
      ok: true,
    },
    {
      icon: "⏰",
      label: "定时任务",
      detail: `${stats.total_cron_jobs} 个 / ${stats.enabled_cron_jobs} 启用`,
      ok: true,
    },
    {
      icon: "📋",
      label: "Cron 执行",
      detail: `${stats.cron_executions - stats.cron_failures} 成功 / ${stats.cron_failures} 失败`,
      ok: stats.cron_failures === 0,
    },
    {
      icon: "🤖",
      label: "SubAgent",
      detail: `${stats.sub_agent_tasks - stats.sub_agent_failures} 完成 / ${stats.sub_agent_failures} 失败`,
      ok: stats.sub_agent_failures === 0,
    },
    {
      icon: "🔧",
      label: "工具耗时",
      detail: `avg ${stats.avg_tool_duration_ms.toFixed(0)}ms`,
      ok: true,
    },
  ];

  return (
    <div className="rounded-2xl border border-gray-100 bg-white p-5 shadow-sm">
      <h3 className="mb-4 flex items-center gap-2 text-sm font-semibold text-gray-700">
        <span>📡</span> 系统状态
      </h3>
      <div className="space-y-3">
        {items.map((item) => (
          <div
            key={item.label}
            className="flex items-center justify-between rounded-xl bg-gray-50 px-4 py-3"
          >
            <div className="flex items-center gap-3">
              <span className="text-lg">{item.icon}</span>
              <span className="text-sm font-medium text-gray-700">
                {item.label}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-500">{item.detail}</span>
              <span
                className={clsx(
                  "h-2 w-2 rounded-full",
                  item.ok ? "bg-emerald-400" : "bg-red-400"
                )}
              />
            </div>
          </div>
        ))}

        {/* 重载配置 */}
        <div className="flex items-center justify-between rounded-xl bg-gray-50 px-4 py-3">
          <div className="flex items-center gap-3">
            <span className="text-lg">🔄</span>
            <span className="text-sm font-medium text-gray-700">配置</span>
          </div>
          <div className="flex items-center gap-2">
            {reloadResult && (
              <span className={clsx(
                "text-xs",
                reloadResult === "重载失败" ? "text-red-500" : "text-emerald-600"
              )}>
                {reloadResult}
              </span>
            )}
            <button
              onClick={handleReloadConfig}
              disabled={reloading}
              className={clsx(
                "flex items-center gap-1 rounded-lg border border-gray-200 px-2.5 py-1 text-xs font-medium text-gray-600 transition-colors hover:bg-white hover:text-violet-600 hover:border-violet-300",
                reloading && "opacity-50"
              )}
            >
              <RotateCcw size={12} className={clsx(reloading && "animate-spin")} />
              重载 config.yaml
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── 主页面 ──────────────────────────────────────────────────

export default function DashboardPage() {
  const {
    data: stats,
    isLoading,
    isFetching,
    refetch,
    dataUpdatedAt,
  } = useQuery<DashboardStats>({
    queryKey: ["dashboard-stats"],
    queryFn: getDashboardStats,
    refetchInterval: 30_000,
    staleTime: 10_000,
  });

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
              <span className="mr-2">⚡</span>系统监控
            </h1>
            <p className="mt-1 text-sm text-gray-400">
              实时系统运行指标概览
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-gray-400">
              更新于 {lastUpdated}
            </span>
            <button
              onClick={() => refetch()}
              disabled={isFetching}
              className={clsx(
                "flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-50",
                isFetching && "opacity-50"
              )}
            >
              <RefreshCw
                size={14}
                className={clsx(isFetching && "animate-spin")}
              />
              刷新
            </button>
          </div>
        </div>

        {/* Loading state */}
        {isLoading && (
          <div className="flex h-64 items-center justify-center">
            <Loader2 size={32} className="animate-spin text-gray-300" />
          </div>
        )}

        {/* Dashboard content */}
        {stats && (
          <div className="space-y-6">
            {/* ── Top 指标卡片 ─────────────────────────────── */}
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
              <StatCard
                icon="📊"
                label="会话"
                value={stats.total_sessions}
                gradient="bg-gradient-to-br from-violet-500 to-blue-600"
              />
              <StatCard
                icon="💬"
                label="消息"
                value={stats.total_messages}
                gradient="bg-gradient-to-br from-blue-500 to-cyan-500"
              />
              <StatCard
                icon="🔤"
                label="Tokens"
                value={stats.total_tokens}
                gradient="bg-gradient-to-br from-cyan-500 to-emerald-500"
              />
              <StatCard
                icon="🔧"
                label="工具调用"
                value={stats.total_tool_calls}
                gradient="bg-gradient-to-br from-orange-400 to-amber-500"
              />
              <StatCard
                icon={stats.total_errors === 0 ? "✅" : "⚠️"}
                label="错误"
                value={stats.total_errors}
                gradient={
                  stats.total_errors === 0
                    ? "bg-gradient-to-br from-emerald-400 to-green-500"
                    : "bg-gradient-to-br from-red-500 to-pink-500"
                }
              />
            </div>

            {/* ── 中部：渠道分布 + 模型使用 ───────────────── */}
            <div className="grid gap-4 md:grid-cols-2">
              <DistributionCard
                title="渠道分布"
                icon="📡"
                data={stats.sessions_by_channel}
              />
              <DistributionCard
                title="模型使用"
                icon="🧠"
                data={stats.models_in_use}
              />
            </div>

            {/* ── 底部：工具排行 + 系统状态 ───────────────── */}
            <div className="grid gap-4 md:grid-cols-2">
              <ToolRankingCard data={stats.tool_calls_by_name} />
              <SystemStatusCard stats={stats} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
