import { useState, useCallback } from "react";
import {
  CalendarClock, Plus, Trash2, Edit2, ChevronDown, ChevronRight,
  CheckCircle2, XCircle, Loader2, Clock, Bot, X, Terminal,
  MessageSquare, RefreshCw,
} from "lucide-react";
import clsx from "clsx";
import {
  useCronJobs,
  useCreateCronJob,
  useUpdateCronJob,
  useDeleteCronJob,
  useToggleCronJob,
  useCronExecutions,
  useCronExecutionDetail,
} from "../hooks/useCron";
import type { CronJobInfo, CronJobCreate, CronJobUpdate } from "../api";

// ── Helpers ────────────────────────────────────────────────

function formatRelativeTime(isoStr: string | null): string {
  if (!isoStr) return "—";
  const diff = new Date(isoStr).getTime() - Date.now();
  const abs  = Math.abs(diff);
  if (abs < 60_000)       return diff > 0 ? "即将执行" : "刚刚";
  if (abs < 3_600_000)  { const m = Math.round(abs / 60_000);    return diff > 0 ? `${m} 分钟后`  : `${m} 分钟前`;  }
  if (abs < 86_400_000) { const h = Math.round(abs / 3_600_000); return diff > 0 ? `${h} 小时后`  : `${h} 小时前`;  }
  const d = Math.round(abs / 86_400_000);
  return diff > 0 ? `${d} 天后` : `${d} 天前`;
}

function formatDuration(start: string | null, end: string | null): string {
  if (!start || !end) return "";
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 1_000)  return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1_000).toFixed(1)}s`;
  return `${Math.round(ms / 60_000)}m`;
}

const SCHEDULE_PRESETS = [
  { label: "每分钟",           value: "* * * * *"   },
  { label: "每 5 分钟",        value: "*/5 * * * *"  },
  { label: "每 15 分钟",       value: "*/15 * * * *" },
  { label: "每小时",           value: "0 * * * *"    },
  { label: "每天 09:00",       value: "0 9 * * *"    },
  { label: "每天 00:00",       value: "0 0 * * *"    },
  { label: "每周一 09:00",     value: "0 9 * * 1"    },
  { label: "每月 1 日 09:00",  value: "0 9 1 * *"    },
];

function describeSchedule(s: string): string {
  return SCHEDULE_PRESETS.find((p) => p.value === s)?.label ?? s;
}

const STATUS_CFG = {
  pending: { Icon: Clock,        color: "text-yellow-500", label: "等待中" },
  running: { Icon: Loader2,      color: "text-blue-500",   label: "执行中" },
  done:    { Icon: CheckCircle2, color: "text-green-500",  label: "已完成" },
  failed:  { Icon: XCircle,      color: "text-red-500",    label: "失败"   },
} as const;

type StatusKey = keyof typeof STATUS_CFG;

// ── Toggle Switch ──────────────────────────────────────────
function Toggle({ enabled, onChange, disabled }: {
  enabled: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={(e) => { e.stopPropagation(); !disabled && onChange(!enabled); }}
      disabled={disabled}
      className={clsx(
        "relative inline-flex h-5 w-9 items-center rounded-full transition-colors focus:outline-none shrink-0",
        enabled ? "bg-nimo-500" : "bg-gray-200",
        disabled && "opacity-50 cursor-not-allowed",
      )}
    >
      <span className={clsx(
        "inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform",
        enabled ? "translate-x-4" : "translate-x-0.5",
      )} />
    </button>
  );
}

// ── Execution Detail Modal ─────────────────────────────────
function ExecutionDetailModal({ executionId, onClose }: {
  executionId: string;
  onClose: () => void;
}) {
  const { data, isLoading } = useCronExecutionDetail(executionId);
  const cfg = STATUS_CFG[(data?.status as StatusKey) ?? "pending"];

  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-xl w-full max-w-2xl max-h-[85vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b shrink-0">
          <CalendarClock size={18} className="text-nimo-500 shrink-0" />
          <div className="flex-1 min-w-0">
            <p className="font-semibold text-gray-800 text-sm truncate">
              {data?.cron_job_name ?? "执行详情"}
            </p>
            {data && (
              <p className="text-xs text-gray-400">
                {new Date(data.started_at ?? "").toLocaleString("zh-CN")}
                {data.finished_at && (
                  <span className="ml-1">
                    · 耗时 {formatDuration(data.started_at, data.finished_at)}
                  </span>
                )}
              </p>
            )}
          </div>
          {cfg && (
            <span className={clsx("flex items-center gap-1 text-xs font-medium shrink-0", cfg.color)}>
              <cfg.Icon
                size={13}
                className={data?.status === "running" ? "animate-spin" : ""}
              />
              {cfg.label}
            </span>
          )}
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 ml-1 shrink-0">
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4 min-h-0">
          {isLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 size={24} className="animate-spin text-gray-300" />
            </div>
          ) : !data ? (
            <p className="text-gray-400 text-sm text-center py-8">加载失败</p>
          ) : (
            <>
              {/* Result */}
              {data.result && (
                <div className="bg-green-50 border border-green-100 rounded-xl p-3.5">
                  <p className="text-xs font-semibold text-green-700 mb-1">执行结果</p>
                  <p className="text-sm text-gray-700 whitespace-pre-wrap">{data.result}</p>
                </div>
              )}

              {/* Error */}
              {data.error && (
                <div className="bg-red-50 border border-red-100 rounded-xl p-3.5">
                  <p className="text-xs font-semibold text-red-700 mb-1">错误信息</p>
                  <p className="text-xs text-red-600 font-mono whitespace-pre-wrap">{data.error}</p>
                </div>
              )}

              {/* Execution log */}
              {data.execution_log.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
                    执行日志
                  </p>
                  <div className="space-y-2">
                    {data.execution_log.map((entry, i) => {
                      const role    = (entry.role as string) ?? "";
                      const content = (entry.content as string) ?? "";
                      const toolCalls = entry.tool_calls as
                        | Array<{ function: { name: string; arguments: string } }>
                        | undefined;

                      if (role === "system") return null;

                      if (role === "user") {
                        return (
                          <div key={i} className="flex gap-2.5">
                            <div className="w-5 h-5 rounded-full bg-gray-200 flex items-center justify-center shrink-0 mt-0.5">
                              <MessageSquare size={10} className="text-gray-500" />
                            </div>
                            <div className="bg-gray-50 border border-gray-100 rounded-xl px-3 py-2 text-sm text-gray-700 flex-1 whitespace-pre-wrap">
                              {content}
                            </div>
                          </div>
                        );
                      }

                      if (role === "assistant") {
                        return (
                          <div key={i} className="space-y-1.5">
                            {content && (
                              <div className="flex gap-2.5">
                                <div className="w-5 h-5 rounded-full bg-nimo-100 flex items-center justify-center shrink-0 mt-0.5">
                                  <Bot size={10} className="text-nimo-500" />
                                </div>
                                <div className="bg-nimo-50 border border-nimo-100 rounded-xl px-3 py-2 text-sm text-gray-700 flex-1 whitespace-pre-wrap">
                                  {content}
                                </div>
                              </div>
                            )}
                            {toolCalls?.map((tc, j) => {
                              let args = tc.function.arguments;
                              try { args = JSON.stringify(JSON.parse(args), null, 2); } catch { /* keep raw */ }
                              return (
                                <div key={j} className="flex gap-2.5 ml-7">
                                  <div className="bg-amber-50 border border-amber-100 rounded-xl px-3 py-2 text-xs font-mono flex-1">
                                    <span className="text-amber-600 font-semibold">{tc.function.name}</span>
                                    <pre className="text-gray-600 mt-1 whitespace-pre-wrap text-xs overflow-x-auto">{args}</pre>
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        );
                      }

                      if (role === "tool") {
                        return (
                          <div key={i} className="flex gap-2.5 ml-7">
                            <div className="w-5 h-5 rounded-full bg-amber-100 flex items-center justify-center shrink-0 mt-0.5">
                              <Terminal size={10} className="text-amber-600" />
                            </div>
                            <div className="bg-white border border-amber-100 rounded-xl px-3 py-2 text-xs text-gray-600 font-mono flex-1 whitespace-pre-wrap overflow-x-auto">
                              {content}
                            </div>
                          </div>
                        );
                      }

                      return null;
                    })}
                  </div>
                </div>
              )}

              {data.execution_log.length === 0 && !data.result && !data.error && (
                <p className="text-gray-400 text-sm text-center py-8">暂无日志记录</p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Execution List ─────────────────────────────────────────
function ExecutionList({ jobId }: { jobId: string }) {
  const { data: executions = [], isLoading } = useCronExecutions(jobId);
  const [detailId, setDetailId] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className="flex items-center gap-1.5 text-xs text-gray-400 py-2">
        <Loader2 size={12} className="animate-spin" /> 加载执行记录…
      </div>
    );
  }

  if (executions.length === 0) {
    return <p className="text-xs text-gray-400 py-2">暂无执行记录</p>;
  }

  return (
    <>
      <div className="space-y-1.5">
        {executions.map((exec) => {
          const cfg = STATUS_CFG[(exec.status as StatusKey) ?? "pending"];
          return (
            <div
              key={exec.id}
              className="flex items-center gap-2.5 px-3 py-2 rounded-lg bg-gray-50 hover:bg-gray-100 transition-colors"
            >
              <cfg.Icon
                size={13}
                className={clsx(cfg.color, exec.status === "running" && "animate-spin")}
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 text-xs">
                  <span className={clsx("font-medium", cfg.color)}>{cfg.label}</span>
                  {exec.agent_name && (
                    <span className="text-gray-400 flex items-center gap-0.5">
                      <Bot size={10} />{exec.agent_name}
                    </span>
                  )}
                  <span className="text-gray-400 ml-auto">
                    {exec.started_at
                      ? new Date(exec.started_at).toLocaleString("zh-CN", {
                          month: "numeric", day: "numeric",
                          hour: "2-digit",  minute: "2-digit",
                        })
                      : "—"}
                  </span>
                  {exec.finished_at && (
                    <span className="text-gray-400">
                      {formatDuration(exec.started_at, exec.finished_at)}
                    </span>
                  )}
                </div>
                {exec.result && (
                  <p className="text-xs text-gray-500 truncate mt-0.5">{exec.result}</p>
                )}
                {exec.error && (
                  <p className="text-xs text-red-400 truncate mt-0.5">{exec.error}</p>
                )}
              </div>
              <button
                onClick={() => setDetailId(exec.id)}
                className="text-xs text-nimo-500 hover:text-nimo-700 hover:bg-nimo-50 px-2 py-1 rounded transition-colors shrink-0"
              >
                日志
              </button>
            </div>
          );
        })}
      </div>

      {detailId && (
        <ExecutionDetailModal
          executionId={detailId}
          onClose={() => setDetailId(null)}
        />
      )}
    </>
  );
}

// ── Cron Form Modal ────────────────────────────────────────
interface FormState {
  name: string;
  schedule: string;
  task_prompt: string;
  agent_name: string;
  enabled: boolean;
  notify_main: boolean;
}

const EMPTY_FORM: FormState = {
  name: "",
  schedule: "0 9 * * *",
  task_prompt: "",
  agent_name: "",
  enabled: true,
  notify_main: true,
};

function CronFormModal({
  initial,
  onClose,
  onSubmit,
  submitting,
}: {
  initial?: Partial<FormState>;
  onClose: () => void;
  onSubmit: (data: CronJobCreate) => void;
  submitting: boolean;
}) {
  const isEdit = initial !== undefined;
  const [form, setForm] = useState<FormState>({
    ...EMPTY_FORM,
    ...initial,
    agent_name: initial?.agent_name ?? "",
  });
  const [scheduleError, setScheduleError] = useState("");

  const validateSchedule = (s: string): boolean => {
    if (s.trim().split(/\s+/).length !== 5) {
      setScheduleError("需要 5 个字段（分 时 日 月 周），如：0 9 * * *");
      return false;
    }
    setScheduleError("");
    return true;
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim() || !form.task_prompt.trim()) return;
    if (!validateSchedule(form.schedule)) return;
    onSubmit({
      name:       form.name.trim(),
      schedule:   form.schedule.trim(),
      task_prompt: form.task_prompt.trim(),
      agent_name: form.agent_name.trim() || null,
      enabled:    form.enabled,
      notify_main: form.notify_main,
    });
  };

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((f) => ({ ...f, [k]: v }));

  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-xl w-full max-w-lg"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b">
          <h2 className="font-semibold text-gray-800 text-sm">
            {isEdit ? "编辑定时任务" : "新建定时任务"}
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="px-5 py-4 space-y-4">
          {/* Name */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              任务名称 <span className="text-red-400">*</span>
            </label>
            <input
              value={form.name}
              onChange={(e) => set("name", e.target.value)}
              placeholder="如：每日早报摘要"
              required
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-nimo-400 transition-colors"
            />
          </div>

          {/* Schedule */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              执行周期（Cron 表达式）<span className="text-red-400">*</span>
            </label>
            <div className="flex gap-2">
              <input
                value={form.schedule}
                onChange={(e) => {
                  set("schedule", e.target.value);
                  if (scheduleError) validateSchedule(e.target.value);
                }}
                placeholder="0 9 * * *"
                required
                className={clsx(
                  "flex-1 rounded-lg border px-3 py-2 text-sm font-mono outline-none transition-colors",
                  scheduleError
                    ? "border-red-300 focus:border-red-400"
                    : "border-gray-200 focus:border-nimo-400",
                )}
              />
              <select
                value=""
                onChange={(e) => {
                  if (!e.target.value) return;
                  set("schedule", e.target.value);
                  validateSchedule(e.target.value);
                }}
                className="rounded-lg border border-gray-200 px-2 py-2 text-xs text-gray-600 outline-none focus:border-nimo-400 bg-white"
              >
                <option value="">快速选择</option>
                {SCHEDULE_PRESETS.map((p) => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
            </div>
            {scheduleError
              ? <p className="text-xs text-red-500 mt-1">{scheduleError}</p>
              : form.schedule && (
                  <p className="text-xs text-gray-400 mt-1">
                    ≈ {describeSchedule(form.schedule)}
                  </p>
                )
            }
          </div>

          {/* Task prompt */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              任务提示词 <span className="text-red-400">*</span>
            </label>
            <textarea
              value={form.task_prompt}
              onChange={(e) => set("task_prompt", e.target.value)}
              placeholder="描述 Agent 要完成的任务，如：搜索今天的科技新闻，整理成简报并保存到工作空间"
              required
              rows={4}
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-nimo-400 transition-colors resize-none"
            />
          </div>

          {/* Agent name */}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">
              指定 SubAgent
              <span className="text-gray-400 font-normal ml-1">（可选，留空则自动匹配）</span>
            </label>
            <input
              value={form.agent_name}
              onChange={(e) => set("agent_name", e.target.value)}
              placeholder="如：researcher"
              className="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-nimo-400 transition-colors"
            />
          </div>

          {/* Checkboxes */}
          <div className="flex items-center gap-6 pt-1">
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={form.notify_main}
                onChange={(e) => set("notify_main", e.target.checked)}
                className="w-3.5 h-3.5 accent-nimo-500"
              />
              <span className="text-xs text-gray-600">完成后通知主 Agent</span>
            </label>
            <label className="flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => set("enabled", e.target.checked)}
                className="w-3.5 h-3.5 accent-nimo-500"
              />
              <span className="text-xs text-gray-600">立即启用</span>
            </label>
          </div>

          {/* Footer */}
          <div className="flex justify-end gap-2 pt-2 border-t border-gray-100">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm rounded-lg text-gray-600 hover:bg-gray-100 transition-colors"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={submitting || !form.name.trim() || !form.task_prompt.trim()}
              className="px-4 py-2 text-sm rounded-lg bg-nimo-500 text-white hover:bg-nimo-600 disabled:opacity-40 transition-colors flex items-center gap-1.5"
            >
              {submitting && <Loader2 size={14} className="animate-spin" />}
              {isEdit ? "保存" : "创建"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Cron Job Card ──────────────────────────────────────────
function CronJobCard({
  job, onEdit, onDelete, onToggle, togglePending, deletePending,
}: {
  job: CronJobInfo;
  onEdit: () => void;
  onDelete: () => void;
  onToggle: (enabled: boolean) => void;
  togglePending: boolean;
  deletePending: boolean;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className={clsx(
      "rounded-xl border transition-all",
      job.enabled ? "bg-white border-gray-200" : "bg-gray-50 border-gray-100",
    )}>
      {/* Header */}
      <div
        className="flex items-start gap-3 p-4 cursor-pointer hover:bg-black/[0.02] transition-colors"
        onClick={() => setOpen((o) => !o)}
      >
        {/* Icon */}
        <div className={clsx(
          "w-10 h-10 rounded-lg flex items-center justify-center shrink-0",
          job.enabled ? "bg-nimo-100 text-nimo-500" : "bg-gray-100 text-gray-400",
        )}>
          <CalendarClock size={20} />
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={clsx(
              "text-sm font-semibold",
              job.enabled ? "text-gray-800" : "text-gray-400",
            )}>
              {job.name}
            </span>
            <code className="text-xs bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded font-mono">
              {job.schedule}
            </code>
            {job.agent_name && (
              <span className="text-xs bg-purple-50 text-purple-600 px-1.5 py-0.5 rounded flex items-center gap-0.5">
                <Bot size={10} />{job.agent_name}
              </span>
            )}
            {!job.enabled && (
              <span className="text-xs bg-gray-100 text-gray-400 px-1.5 py-0.5 rounded">已停用</span>
            )}
          </div>
          <p className="text-xs text-gray-400 mt-0.5 line-clamp-1">{job.task_prompt}</p>
          <div className="flex items-center gap-3 mt-1.5 text-xs text-gray-400">
            {job.next_run_at && job.enabled && (
              <span className="flex items-center gap-1">
                <Clock size={10} className="text-nimo-400" />
                下次 {formatRelativeTime(job.next_run_at)}
              </span>
            )}
            {job.last_run_at && (
              <span>上次 {formatRelativeTime(job.last_run_at)}</span>
            )}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1 shrink-0">
          <Toggle enabled={job.enabled} onChange={onToggle} disabled={togglePending} />
          <button
            onClick={(e) => { e.stopPropagation(); onEdit(); }}
            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
            title="编辑"
          >
            <Edit2 size={14} />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            disabled={deletePending}
            className="p-1.5 rounded-lg text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors"
            title="删除"
          >
            {deletePending
              ? <Loader2 size={14} className="animate-spin" />
              : <Trash2 size={14} />}
          </button>
          {open
            ? <ChevronDown size={14} className="text-gray-400" />
            : <ChevronRight size={14} className="text-gray-400" />}
        </div>
      </div>

      {/* Expanded: execution history */}
      {open && (
        <div className="px-4 pb-4 border-t border-gray-100 pt-3">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
            执行记录
          </p>
          <ExecutionList jobId={job.id} />
        </div>
      )}
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────
export default function CronPage() {
  const { data: jobs = [], isLoading } = useCronJobs();
  const createJob = useCreateCronJob();
  const updateJob = useUpdateCronJob();
  const deleteJob = useDeleteCronJob();
  const toggleJob = useToggleCronJob();

  const [editingJob, setEditingJob] = useState<CronJobInfo | null>(null);
  const [showCreate,  setShowCreate]  = useState(false);

  const enabledCount = jobs.filter((j) => j.enabled).length;

  const handleCreate = useCallback(async (data: CronJobCreate) => {
    await createJob.mutateAsync(data);
    setShowCreate(false);
  }, [createJob]);

  const handleUpdate = useCallback(async (data: CronJobCreate) => {
    if (!editingJob) return;
    const patch: CronJobUpdate = {
      name:        data.name,
      schedule:    data.schedule,
      task_prompt: data.task_prompt,
      agent_name:  data.agent_name,
      notify_main: data.notify_main,
      enabled:     data.enabled,
    };
    await updateJob.mutateAsync({ id: editingJob.id, data: patch });
    setEditingJob(null);
  }, [editingJob, updateJob]);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* ── Header ── */}
      <div className="px-6 py-4 bg-white border-b">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="font-semibold text-gray-800 flex items-center gap-2">
              <CalendarClock size={20} className="text-nimo-500" />
              定时任务
            </h1>
            <p className="text-xs text-gray-400 mt-0.5">
              配置 Agent 自动执行的周期性任务 · {enabledCount}/{jobs.length} 已启用
            </p>
          </div>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-1.5 text-xs text-white bg-nimo-500 hover:bg-nimo-600 px-3 py-2 rounded-lg transition-colors"
          >
            <Plus size={14} /> 新建任务
          </button>
        </div>
      </div>

      {/* ── Job list ── */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {isLoading ? (
          <div className="flex items-center justify-center py-24">
            <Loader2 size={24} className="animate-spin text-gray-300" />
          </div>
        ) : jobs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 text-gray-400 gap-4">
            <CalendarClock size={48} className="text-gray-200" />
            <div className="text-center">
              <p className="text-base font-medium">暂无定时任务</p>
              <p className="text-sm mt-1">
                创建定时任务，让 Agent 自动完成数据收集、摘要生成等周期性工作
              </p>
            </div>
            <button
              onClick={() => setShowCreate(true)}
              className="flex items-center gap-1.5 text-sm text-nimo-500 hover:text-nimo-700 border border-nimo-200 hover:border-nimo-400 px-4 py-2 rounded-lg transition-colors"
            >
              <Plus size={15} /> 创建第一个任务
            </button>
          </div>
        ) : (
          <div className="space-y-3 max-w-3xl mx-auto">
            {jobs.map((job) => (
              <CronJobCard
                key={job.id}
                job={job}
                onEdit={() => setEditingJob(job)}
                onDelete={() => {
                  if (confirm(`确定删除任务"${job.name}"？所有执行记录将一并删除。`)) {
                    deleteJob.mutate(job.id);
                  }
                }}
                onToggle={(enabled) => toggleJob.mutate({ id: job.id, enabled })}
                togglePending={toggleJob.isPending}
                deletePending={deleteJob.isPending}
              />
            ))}
          </div>
        )}
      </div>

      {/* ── Footer ── */}
      <div className="px-6 py-3 bg-white border-t">
        <p className="text-xs text-gray-400 flex items-center gap-1.5">
          <RefreshCw size={12} />
          调度器每 30 秒检查一次到期任务，实际触发时间最多有 30 秒偏差
        </p>
      </div>

      {/* ── Modals ── */}
      {showCreate && (
        <CronFormModal
          onClose={() => setShowCreate(false)}
          onSubmit={handleCreate}
          submitting={createJob.isPending}
        />
      )}
      {editingJob && (
        <CronFormModal
          initial={{
            name:        editingJob.name,
            schedule:    editingJob.schedule,
            task_prompt: editingJob.task_prompt,
            agent_name:  editingJob.agent_name ?? "",
            enabled:     editingJob.enabled,
            notify_main: editingJob.notify_main,
          }}
          onClose={() => setEditingJob(null)}
          onSubmit={handleUpdate}
          submitting={updateJob.isPending}
        />
      )}
    </div>
  );
}
