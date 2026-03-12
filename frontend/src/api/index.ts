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

export interface SessionSummary {
  id: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface HistoryMessage {
  role: string;
  content: string;
  created_at: string;
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

export async function getSessions(channel = "web"): Promise<SessionSummary[]> {
  const { data } = await api.get<SessionSummary[]>("/sessions", { params: { channel } });
  return data;
}

export async function createSession(channel = "web"): Promise<{ id: string }> {
  const { data } = await api.post<{ id: string }>("/sessions", null, { params: { channel } });
  return data;
}

export async function getSessionMessages(sessionId: string): Promise<HistoryMessage[]> {
  const { data } = await api.get<HistoryMessage[]>(`/sessions/${sessionId}/messages`);
  return data;
}

export async function deleteSession(sessionId: string): Promise<void> {
  await api.delete(`/sessions/${sessionId}`);
}

// ── Workspace API ─────────────────────────────────────────

export interface WorkspaceFileContent {
  name: string;
  content: string;
}

export interface WorkspaceInfo {
  workspace_dir: string;
  exists: boolean;
  bootstrapped: boolean;
  files: Record<string, boolean>;
}

export interface CanvasFileMeta {
  name: string;
  size: number;
  modified_at: string;
}

export async function getWorkspaceInfo(): Promise<WorkspaceInfo> {
  const { data } = await api.get<WorkspaceInfo>("/workspace/info");
  return data;
}

export async function getWorkspaceFiles(): Promise<string[]> {
  const { data } = await api.get<string[]>("/workspace/files");
  return data;
}

export async function getWorkspaceFile(name: string): Promise<WorkspaceFileContent> {
  const { data } = await api.get<WorkspaceFileContent>(`/workspace/files/${name}`);
  return data;
}

export async function updateWorkspaceFile(name: string, content: string): Promise<WorkspaceFileContent> {
  const { data } = await api.put<WorkspaceFileContent>(`/workspace/files/${name}`, { content });
  return data;
}

export async function appendWorkspaceMemory(text: string): Promise<void> {
  await api.post("/workspace/memory", { text });
}

export async function getDailyLog(date?: string): Promise<WorkspaceFileContent> {
  const { data } = await api.get<WorkspaceFileContent>("/workspace/memory/daily", {
    params: date ? { date } : {},
  });
  return data;
}

export async function listDailyLogs(): Promise<string[]> {
  const { data } = await api.get<string[]>("/workspace/memory/daily/list");
  return data;
}

export async function listCanvas(): Promise<CanvasFileMeta[]> {
  const { data } = await api.get<CanvasFileMeta[]>("/workspace/canvas");
  return data;
}

export async function getCanvasFile(filename: string): Promise<WorkspaceFileContent> {
  const { data } = await api.get<WorkspaceFileContent>(`/workspace/canvas/${filename}`);
  return data;
}

// ── Session Meta & Config API ─────────────────────────────

export interface SessionMeta {
  id: string;
  channel: string;
  model_name: string | null;
  context_tokens: number | null;
  enabled_tools: string[] | null;
  enabled_skills: string[] | null;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface SessionConfigUpdate {
  enabled_tools?: string[] | null;
  enabled_skills?: string[] | null;
  model_name?: string | null;
}

export interface ServiceConfig {
  config: Record<string, unknown>;
  path: string;
}

export async function getSessionMeta(sessionId: string): Promise<SessionMeta> {
  const { data } = await api.get<SessionMeta>(`/sessions/${sessionId}/meta`);
  return data;
}

export async function updateSessionConfig(
  sessionId: string,
  config: SessionConfigUpdate
): Promise<SessionMeta> {
  const { data } = await api.patch<SessionMeta>(`/sessions/${sessionId}/config`, config);
  return data;
}

export async function getServiceConfig(): Promise<ServiceConfig> {
  const { data } = await api.get<ServiceConfig>("/config");
  return data;
}

export async function updateServiceConfig(
  config: Record<string, unknown>
): Promise<ServiceConfig> {
  const { data } = await api.put<ServiceConfig>("/config", { config });
  return data;
}

export default api;
