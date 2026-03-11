import axios from "axios";

const api = axios.create({
  baseURL: "/api/v1",
  headers: { "Content-Type": "application/json" },
});

// ── Types ─────────────────────────────────────────────────

export interface ChatRequest {
  session_id: string;
  message: string;
  channel?: string;
  user_id?: string;
}

export interface ChatResponse {
  session_id: string;
  reply: string;
}

export interface TaskStatusResponse {
  task_id: string;
  status: "pending" | "running" | "done" | "failed" | "cancelled";
  result: string | null;
  error: string | null;
}

// ── API 方法 ──────────────────────────────────────────────

export async function chat(req: ChatRequest): Promise<ChatResponse> {
  const { data } = await api.post<ChatResponse>("/agent/chat", req);
  return data;
}

export async function getTaskStatus(taskId: string): Promise<TaskStatusResponse> {
  const { data } = await api.get<TaskStatusResponse>(`/agent/tasks/${taskId}`);
  return data;
}

export async function clearMemory(sessionId: string): Promise<void> {
  await api.delete(`/sessions/${sessionId}/memory`);
}

export default api;
