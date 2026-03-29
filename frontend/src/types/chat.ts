// ── Chat Page Types ─────────────────────────────────────

export interface ToolCallEntry {
  id: string;
  name: string;
  input: Record<string, unknown>;
  output?: string;
  error?: string | null;
  duration_ms?: number;
  status: "running" | "done" | "cancelled";
}

export interface FileAttachment {
  url: string;
  name: string;
  mime: string;
}

export interface ToolGuardRequest {
  requestId: string;
  toolName: string;
  toolInput: Record<string, unknown>;
  level: number;
  rule: string | null;
  threshold: number;
  description: string;
  status: "pending" | "approved" | "rejected";
}

export interface PlanStep {
  index: number;
  title: string;
  description: string;
  strategy: string;
  tools: string[];
  status: string;  // pending | running | completed | failed | skipped
  task_id?: string | null;
  result?: string | null;
  error?: string | null;
}

export interface PlanData {
  id: string;
  goal: string;
  summary: string;
  status: string;  // generating | confirming | executing | completed | cancelled | failed
  steps: PlanStep[];
  current_step: number;
}

export interface RetryEntry {
  attempt: number;
  maxAttempts: number;
  error: string;
  delay: number;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  images?: string[];  // base64 data URI 列表（用户发送的图片）
  thinking?: string;
  streamingThinking?: boolean;
  toolCalls?: ToolCallEntry[];
  files?: FileAttachment[];
  streaming?: boolean;
  guardRequest?: ToolGuardRequest;
  retries?: RetryEntry[];
  compressed?: boolean;         // 旧消息，灰色/半透明
  isContextSummary?: boolean;   // 摘要分隔线
  compressedCount?: number;     // 压缩了几条
  cardType?: string;            // "sub_agent_result" | "cron_result"
  cardMeta?: Record<string, unknown>;  // agent_name, task_id, job_name 等
  plan?: PlanData;
  planGenerating?: boolean;
}
