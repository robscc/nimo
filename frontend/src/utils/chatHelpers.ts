import { getActivePlan, getSessionMessages } from "../api";
import type { Message } from "../types/chat";

// ── Shell Output Parser ─────────────────────────────────

export type ShellParsed =
  | { type: "error"; message: string }
  | { type: "result"; returncode: number | null; stdout: string; stderr: string };

export function parseShellOutput(raw: string): ShellParsed {
  const errorMatch = raw.match(/<error>([\s\S]*?)<\/error>/);
  if (errorMatch) return { type: "error", message: errorMatch[1].trim() };

  const rc = raw.match(/<returncode>(\d+)<\/returncode>/);
  const stdout = raw.match(/<stdout>([\s\S]*?)<\/stdout>/)?.[1] ?? "";
  const stderr = raw.match(/<stderr>([\s\S]*?)<\/stderr>/)?.[1] ?? "";
  return {
    type: "result",
    returncode: rc ? parseInt(rc[1], 10) : null,
    stdout: stdout.trim(),
    stderr: stderr.trim(),
  };
}

// ── Tool Guard Level Labels ─────────────────────────────

export const LEVEL_LABELS: Record<number, { label: string; color: string; bg: string; border: string }> = {
  0: { label: "毁灭性", color: "text-red-700", bg: "bg-red-50", border: "border-red-200" },
  1: { label: "高危", color: "text-orange-700", bg: "bg-orange-50", border: "border-orange-200" },
  2: { label: "中危", color: "text-amber-700", bg: "bg-amber-50", border: "border-amber-200" },
  3: { label: "低危", color: "text-blue-700", bg: "bg-blue-50", border: "border-blue-200" },
  4: { label: "安全", color: "text-green-700", bg: "bg-green-50", border: "border-green-200" },
};

// ── History → Messages Mapper ───────────────────────────

export function mapHistoryToMessages(history: Awaited<ReturnType<typeof getSessionMessages>>): Message[] {
  return history.map((m) => {
    const meta = m.meta;
    return {
      role: m.role as "user" | "assistant",
      content: m.content,
      images: m.role === "user" ? meta?.images : undefined,
      thinking: meta?.thinking,
      toolCalls: meta?.tool_calls?.map((tc) => ({
        id: tc.id,
        name: tc.name,
        input: tc.input,
        output: tc.output,
        error: tc.error,
        duration_ms: tc.duration_ms,
        status: "done" as const,
      })),
      files: meta?.files,
      compressed: meta?.compressed ?? false,
      isContextSummary: meta?.type === "context_summary",
      compressedCount: meta?.compressed_count,
      cardType: meta?.card_type as string | undefined,
      cardMeta: meta?.card_type ? meta : undefined,
    };
  });
}

export async function loadHistoryWithPlan(sessionId: string): Promise<Message[]> {
  const [history, activePlan] = await Promise.all([
    getSessionMessages(sessionId),
    getActivePlan(sessionId),
  ]);

  const messages = mapHistoryToMessages(history);
  if (!activePlan) return messages;

  // 优先按历史消息里的 plan_id 锚定到原时间节点（通常是 plan_ready 那条 assistant 消息）
  const anchorIndex = history
    .map((m, idx) => ({ m, idx }))
    .reverse()
    .find(({ m }) => m.role === "assistant" && m.meta?.plan_id === activePlan.id)?.idx;

  if (anchorIndex !== undefined) {
    messages[anchorIndex] = {
      ...messages[anchorIndex],
      plan: activePlan,
      planGenerating: false,
    };
    return messages;
  }

  // 回退：若没有可锚定 plan_id，再挂载到最近一条 assistant 消息
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].role === "assistant") {
      messages[i] = {
        ...messages[i],
        plan: activePlan,
        planGenerating: false,
      };
      return messages;
    }
  }

  // 兜底：若暂无 assistant 消息，则追加一条空 assistant 承载 PlanCard
  messages.push({
    role: "assistant",
    content: "",
    plan: activePlan,
    planGenerating: false,
  });
  return messages;
}
