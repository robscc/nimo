import { useState } from "react";
import clsx from "clsx";
import {
  Loader2, CheckCircle2, XCircle, ChevronDown, ChevronRight,
  ClipboardList, Circle, Info,
} from "lucide-react";
import type { PlanData } from "../../types/chat";

// ── Plan Step Status Icon ───────────────────────────────

function PlanStepStatusIcon({ status }: { status: string }) {
  switch (status) {
    case "running":
      return <Loader2 size={14} className="text-teal-500 animate-spin shrink-0" />;
    case "completed":
      return <CheckCircle2 size={14} className="text-green-500 shrink-0" />;
    case "failed":
      return <XCircle size={14} className="text-red-500 shrink-0" />;
    case "skipped":
      return <CheckCircle2 size={14} className="text-gray-400 shrink-0" />;
    default: // pending
      return <Circle size={14} className="text-gray-300 shrink-0" />;
  }
}

// ── Plan Status Badge ───────────────────────────────────

function PlanStatusBadge({ status }: { status: string }) {
  const config: Record<string, { label: string; cls: string }> = {
    generating: { label: "生成中", cls: "bg-teal-100 text-teal-700" },
    confirming: { label: "待确认", cls: "bg-yellow-100 text-yellow-700" },
    executing:  { label: "执行中", cls: "bg-teal-100 text-teal-700" },
    completed:  { label: "已完成", cls: "bg-green-100 text-green-700" },
    cancelled:  { label: "已取消", cls: "bg-gray-100 text-gray-500" },
    failed:     { label: "失败",   cls: "bg-red-100 text-red-700" },
  };
  const c = config[status] ?? { label: status, cls: "bg-gray-100 text-gray-500" };
  return (
    <span className={clsx("px-1.5 py-0.5 rounded text-[10px] font-semibold", c.cls)}>
      {c.label}
    </span>
  );
}

// ── Plan Card ───────────────────────────────────────────

export default function PlanCard({ plan, generating }: { plan?: PlanData; generating?: boolean }) {
  const [collapsed, setCollapsed] = useState(false);
  const [expandedSteps, setExpandedSteps] = useState<Set<number>>(new Set());

  const toggleStep = (index: number) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  // Skeleton loading state
  if (generating && !plan) {
    return (
      <div className="rounded-xl border border-teal-200 shadow-sm overflow-hidden max-w-[85%]">
        <div className="flex items-center gap-2 px-3.5 py-2 bg-gradient-to-r from-teal-500 to-teal-600">
          <ClipboardList size={15} className="text-white shrink-0" />
          <span className="text-xs font-semibold text-white">执行计划</span>
          <span className="ml-auto">
            <Loader2 size={13} className="text-white/80 animate-spin" />
          </span>
        </div>
        <div className="px-3.5 py-3 space-y-2.5 bg-teal-50/50">
          <div className="h-3.5 w-3/4 rounded bg-teal-100 animate-pulse" />
          <div className="h-3 w-1/2 rounded bg-teal-100/70 animate-pulse" />
          <div className="space-y-1.5 mt-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="flex items-center gap-2">
                <div className="h-3.5 w-3.5 rounded-full bg-teal-100 animate-pulse shrink-0" />
                <div className="h-3 rounded bg-teal-100/60 animate-pulse" style={{ width: `${50 + i * 10}%` }} />
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (!plan) return null;

  return (
    <div className="rounded-xl border border-teal-200 shadow-sm overflow-hidden max-w-[85%]">
      {/* Header */}
      <div className="flex items-center gap-2 px-3.5 py-2 bg-gradient-to-r from-teal-500 to-teal-600">
        <ClipboardList size={15} className="text-white shrink-0" />
        <span className="text-xs font-semibold text-white truncate">执行计划</span>
        <div className="ml-auto flex items-center gap-1.5">
          <PlanStatusBadge status={plan.status} />
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="text-white/70 hover:text-white transition-colors"
          >
            {collapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
          </button>
        </div>
      </div>

      {!collapsed && (
        <>
          {/* Goal & Summary */}
          <div className="px-3.5 py-2.5 border-b border-teal-100 bg-teal-50/50">
            <p className="text-xs font-medium text-teal-800 mb-0.5">🎯 {plan.goal}</p>
            <p className="text-xs text-teal-600 leading-relaxed">{plan.summary}</p>
          </div>

          {/* Steps */}
          <div className="bg-white divide-y divide-gray-100">
            {plan.steps.map((step) => (
              <div key={step.index}>
                <button
                  onClick={() => toggleStep(step.index)}
                  className="w-full flex items-center gap-2 px-3.5 py-2 text-left hover:bg-gray-50 transition-colors"
                >
                  <PlanStepStatusIcon status={step.status} />
                  <span className="text-[11px] font-medium text-gray-400 shrink-0">
                    {step.index + 1}.
                  </span>
                  <span className="text-xs text-gray-700 flex-1 truncate">{step.title}</span>
                  {expandedSteps.has(step.index)
                    ? <ChevronDown size={12} className="text-gray-400 shrink-0" />
                    : <ChevronRight size={12} className="text-gray-400 shrink-0" />}
                </button>
                {expandedSteps.has(step.index) && (
                  <div className="px-3.5 pb-2.5 pl-9 space-y-1">
                    <p className="text-xs text-gray-500 leading-relaxed">{step.description}</p>
                    {step.strategy && (
                      <p className="text-[11px] text-gray-400">
                        <span className="font-medium">策略：</span>{step.strategy}
                      </p>
                    )}
                    {step.tools.length > 0 && (
                      <div className="flex items-center gap-1 flex-wrap">
                        <span className="text-[11px] text-gray-400 font-medium">工具：</span>
                        {step.tools.map((t) => (
                          <span key={t} className="px-1.5 py-0.5 rounded bg-gray-100 text-[10px] text-gray-500 font-mono">
                            {t}
                          </span>
                        ))}
                      </div>
                    )}
                    {step.result && (
                      <p className="text-[11px] text-green-600 leading-relaxed">
                        <span className="font-medium">结果：</span>{step.result}
                      </p>
                    )}
                    {step.error && (
                      <p className="text-[11px] text-red-500 leading-relaxed">
                        <span className="font-medium">错误：</span>{step.error}
                      </p>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Confirming hint */}
          {plan.status === "confirming" && (
            <div className="px-3.5 py-2 bg-yellow-50 border-t border-yellow-200 text-xs text-yellow-700 flex items-center gap-1.5">
              <Info size={12} className="shrink-0" />
              回复「开始执行」启动计划，或提出修改意见
            </div>
          )}
        </>
      )}
    </div>
  );
}
