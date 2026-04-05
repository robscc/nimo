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
      return <CheckCircle2 size={14} className="text-green-600 shrink-0" />;
    case "failed":
      return <XCircle size={14} className="text-red-600 shrink-0" />;
    case "pending":
      return <Circle size={14} className="text-gray-400 shrink-0" />;
    default:
      return <Circle size={14} className="text-gray-400 shrink-0" />;
  }
}

// ── Plan Status Badge ───────────────────────────────────

function PlanStatusBadge({ status }: { status: string }) {
  const styles = {
    planning: "bg-blue-100 text-blue-700",
    confirming: "bg-yellow-100 text-yellow-700",
    executing: "bg-teal-100 text-teal-700",
    completed: "bg-green-100 text-green-700",
    failed: "bg-red-100 text-red-700",
  };

  const labels = {
    planning: "规划中",
    confirming: "待确认",
    executing: "执行中",
    completed: "已完成",
    failed: "失败",
  };

  return (
    <span className={clsx("px-1.5 py-0.5 rounded text-xs font-medium shrink-0", styles[status as keyof typeof styles] || "bg-gray-100 text-gray-700")}>
      {labels[status as keyof typeof labels] || status}
    </span>
  );
}

// ── Plan Step Item ──────────────────────────────────────

function PlanStepItem({ step, index }: { step: any; index: number }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border-b border-gray-100 last:border-0">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-start gap-2.5 px-3.5 py-2.5 text-left hover:bg-gray-50 transition-colors"
      >
        <div className="shrink-0 mt-0.5">
          <PlanStepStatusIcon status={step.status} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-medium text-gray-500 shrink-0">步骤 {index + 1}</span>
            {step.status === "running" && (
              <span className="text-xs text-teal-600 shrink-0">执行中...</span>
            )}
          </div>
          <p className="text-sm text-gray-900 break-words">{step.description}</p>
          {step.result && !expanded && (
            <p className="text-xs text-gray-500 mt-1 truncate">{step.result}</p>
          )}
        </div>
        <div className="shrink-0 mt-0.5">
          {expanded ? <ChevronDown size={16} className="text-gray-400" /> : <ChevronRight size={16} className="text-gray-400" />}
        </div>
      </button>

      {expanded && (
        <div className="px-3.5 pb-2.5 pl-10">
          {step.strategy && (
            <div className="mb-2">
              <div className="text-xs font-medium text-gray-700 mb-1">策略</div>
              <div className="text-xs text-gray-600 bg-gray-50 rounded px-2 py-1.5 break-words overflow-auto max-h-32">
                {step.strategy}
              </div>
            </div>
          )}
          {step.result && (
            <div>
              <div className="text-xs font-medium text-gray-700 mb-1">结果</div>
              <div className="text-xs text-gray-700 bg-green-50 rounded px-2 py-1.5 whitespace-pre-wrap break-words overflow-auto max-h-64">
                {step.result}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main Plan Card ──────────────────────────────────────

export default function PlanCard({ plan }: { plan: PlanData }) {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="max-w-full rounded-lg border border-indigo-200 bg-white shadow-sm overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="w-full flex items-center gap-2.5 px-3.5 py-2.5 bg-indigo-50 border-b border-indigo-200 hover:bg-indigo-100 transition-colors"
      >
        <ClipboardList size={18} className="text-indigo-600 shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-gray-900">执行计划</span>
            <PlanStatusBadge status={plan.status} />
          </div>
          <p className="text-xs text-gray-600 mt-0.5">{plan.steps.length} 个步骤</p>
        </div>
        <div className="shrink-0">
          {collapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
        </div>
      </button>

      {/* Steps */}
      {!collapsed && (
        <>
          <div className="max-h-[500px] overflow-y-auto">
            {plan.steps.map((step, index) => (
              <PlanStepItem key={step.id || index} step={step} index={index} />
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
