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
  images?: string[];  // base64 data URI 列表（多模态图片输入）
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
  agent_name: string | null;
  task_type: string | null;
  task_prompt: string | null;
  priority: number;
  retry_count: number;
  max_retries: number;
  created_at: string | null;
}

export interface TaskListParams {
  status?: string;
  priority_min?: number;
  priority_max?: number;
  parent_session_id?: string;
  limit?: number;
  offset?: number;
}

export interface TaskListResponse {
  items: TaskStatusResponse[];
  total: number;
  limit: number;
  offset: number;
}

export interface SessionSummary {
  id: string;
  title: string;
  channel: string;
  model_name: string | null;
  message_count: number;
  sub_tasks_count: number;
  created_at: string;
  updated_at: string;
}

export interface SubTaskSummary {
  id: string;
  sub_session_id: string;
  task_prompt: string;
  status: "pending" | "running" | "done" | "failed" | "cancelled" | "input_required";
  agent_name: string | null;
  task_type: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface TaskArtifact {
  id: string;
  task_id: string;
  artifact_type: string;
  content: string;
  title: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface HistoryMessageMeta {
  thinking?: string;
  tool_calls?: Array<{
    id: string;
    name: string;
    input: Record<string, unknown>;
    output?: string;
    error?: string | null;
    duration_ms?: number;
    status: string;
  }>;
  files?: Array<{
    url: string;
    name: string;
    mime: string;
  }>;
  images?: string[];  // base64 data URI 列表（用户发送的图片）
  compressed?: boolean;           // 是否已被压缩
  type?: string;                  // 'context_summary' 表示摘要消息
  compressed_count?: number;      // 被压缩的消息数量
  card_type?: string;             // 'sub_agent_result' | 'cron_result'
  agent_name?: string;
  task_id?: string;
  job_name?: string;
  [key: string]: unknown;         // 允许额外字段
}

export interface HistoryMessage {
  role: string;
  content: string;
  created_at: string;
  meta?: HistoryMessageMeta | null;
}

export interface TaskListItem {
  task_id: string;
  status: "pending" | "running" | "done" | "failed" | "cancelled";
  agent_name: string | null;
  task_type: string | null;
  task_prompt: string;
  parent_session_id: string;
  result: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

/** SSE event emitted when an async task (SubAgent / Cron) completes. */
export interface AsyncTaskDoneSSEEvent {
  type: "async_task_done";
  source: "sub_agent" | "cron";
  task_id: string | null;
  agent_name: string | null;
  task_prompt: string;
  status: "done" | "failed";
  result_preview: string | null;
  error_preview: string | null;
  finished_at: string | null;
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

export async function listTasks(params?: TaskListParams): Promise<TaskListResponse> {
  const { data } = await api.get<TaskListResponse>("/agent/tasks", { params });
  return data;
}

export async function clearMemory(sessionId: string): Promise<void> {
  await api.delete(`/sessions/${sessionId}/memory`);
}

export async function getSessions(channel = "web"): Promise<SessionSummary[]> {
  const { data } = await api.get<SessionSummary[]>("/sessions", { params: { channel } });
  return data;
}

export async function getAllSessions(limit = 100): Promise<SessionSummary[]> {
  const { data } = await api.get<SessionSummary[]>("/sessions", { params: { channel: "web", limit } });
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

export async function getSessionSubTasks(sessionId: string): Promise<SubTaskSummary[]> {
  const { data } = await api.get<SubTaskSummary[]>(`/sessions/${sessionId}/sub-tasks`);
  return data;
}

export async function getTaskArtifacts(taskId: string): Promise<TaskArtifact[]> {
  const { data } = await api.get<TaskArtifact[]>(`/tasks/${taskId}/artifacts`);
  return data;
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
  tool_guard_threshold: number | null;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface SessionConfigUpdate {
  enabled_tools?: string[] | null;
  enabled_skills?: string[] | null;
  // model_name 不再通过 Session 配置，统一从 config.yaml 读取
  tool_guard_threshold?: number | null;
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

export async function reloadServiceConfig(): Promise<{ message: string; llm_model: string; llm_provider: string }> {
  const { data } = await api.post("/config/reload");
  return data;
}

// ── SubAgent API ─────────────────────────────────────────

export interface SubAgentInfo {
  name: string;
  display_name: string;
  role_prompt: string;
  accepted_task_types: string[];
  model_name: string | null;
  model_provider: string | null;
  model_base_url: string | null;
  has_custom_model: boolean;
  max_tool_rounds: number;
  timeout_seconds: number;
  enabled: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface SubAgentCreate {
  name: string;
  display_name?: string;
  role_prompt?: string;
  accepted_task_types?: string[];
  model_name?: string | null;
  model_provider?: string | null;
  model_api_key?: string | null;
  model_base_url?: string | null;
  max_tool_rounds?: number;
  timeout_seconds?: number;
  enabled?: boolean;
}

export interface SubAgentUpdate {
  display_name?: string;
  role_prompt?: string;
  accepted_task_types?: string[];
  model_name?: string | null;
  model_provider?: string | null;
  model_api_key?: string | null;
  model_base_url?: string | null;
  max_tool_rounds?: number;
  timeout_seconds?: number;
  enabled?: boolean;
}

export async function listSubAgents(): Promise<SubAgentInfo[]> {
  const { data } = await api.get<SubAgentInfo[]>("/sub-agents");
  return data;
}

export async function getSubAgent(name: string): Promise<SubAgentInfo> {
  const { data } = await api.get<SubAgentInfo>(`/sub-agents/${name}`);
  return data;
}

export async function createSubAgent(payload: SubAgentCreate): Promise<SubAgentInfo> {
  const { data } = await api.post<SubAgentInfo>("/sub-agents", payload);
  return data;
}

export async function updateSubAgent(name: string, payload: SubAgentUpdate): Promise<SubAgentInfo> {
  const { data } = await api.patch<SubAgentInfo>(`/sub-agents/${name}`, payload);
  return data;
}

export async function deleteSubAgent(name: string): Promise<void> {
  await api.delete(`/sub-agents/${name}`);
}

// ── Cron API ─────────────────────────────────────────────

export interface CronJobInfo {
  id: string;
  name: string;
  schedule: string;
  task_prompt: string;
  agent_name: string | null;
  enabled: boolean;
  notify_main: boolean;
  target_session_id: string | null;
  last_run_at: string | null;
  next_run_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface CronJobCreate {
  name: string;
  schedule: string;
  task_prompt: string;
  agent_name?: string | null;
  enabled?: boolean;
  notify_main?: boolean;
  target_session_id?: string | null;
}

export interface CronJobUpdate {
  name?: string;
  schedule?: string;
  task_prompt?: string;
  agent_name?: string | null;
  enabled?: boolean;
  notify_main?: boolean;
  target_session_id?: string | null;
}

export interface CronExecutionInfo {
  id: string;
  cron_job_id: string;
  cron_job_name: string;
  status: string;
  agent_name: string | null;
  started_at: string | null;
  finished_at: string | null;
  result: string | null;
  error: string | null;
}

export interface CronExecutionDetail extends CronExecutionInfo {
  execution_log: Array<Record<string, unknown>>;
}

export async function listCronJobs(): Promise<CronJobInfo[]> {
  const { data } = await api.get<CronJobInfo[]>("/cron");
  return data;
}

export async function getCronJob(jobId: string): Promise<CronJobInfo> {
  const { data } = await api.get<CronJobInfo>(`/cron/${jobId}`);
  return data;
}

export async function createCronJob(payload: CronJobCreate): Promise<CronJobInfo> {
  const { data } = await api.post<CronJobInfo>("/cron", payload);
  return data;
}

export async function updateCronJob(jobId: string, payload: CronJobUpdate): Promise<CronJobInfo> {
  const { data } = await api.patch<CronJobInfo>(`/cron/${jobId}`, payload);
  return data;
}

export async function deleteCronJob(jobId: string): Promise<void> {
  await api.delete(`/cron/${jobId}`);
}

export async function toggleCronJob(jobId: string, enabled: boolean): Promise<CronJobInfo> {
  const { data } = await api.patch<CronJobInfo>(`/cron/${jobId}/toggle`, { enabled });
  return data;
}

export async function listCronExecutions(jobId: string, limit = 20): Promise<CronExecutionInfo[]> {
  const { data } = await api.get<CronExecutionInfo[]>(`/cron/${jobId}/executions`, {
    params: { limit },
  });
  return data;
}

export async function getCronExecutionDetail(executionId: string): Promise<CronExecutionDetail> {
  const { data } = await api.get<CronExecutionDetail>(`/cron/executions/${executionId}/detail`);
  return data;
}

// ── Dashboard API ─────────────────────────────────────────

export interface DashboardStats {
  total_sessions: number;
  sessions_by_channel: Record<string, number>;
  total_messages: number;
  total_tokens: number;
  models_in_use: Record<string, number>;
  total_tool_calls: number;
  tool_calls_by_name: Record<string, number>;
  tool_errors: number;
  avg_tool_duration_ms: number;
  total_skills: number;
  enabled_skills: number;
  total_cron_jobs: number;
  enabled_cron_jobs: number;
  cron_executions: number;
  cron_failures: number;
  total_errors: number;
  sub_agent_tasks: number;
  sub_agent_failures: number;
}

export async function getDashboardStats(): Promise<DashboardStats> {
  const { data } = await api.get<DashboardStats>("/dashboard/stats");
  return data;
}

// ── Tool Guard API ────────────────────────────────────────

export async function resolveToolGuard(
  requestId: string,
  approved: boolean
): Promise<void> {
  await api.post(`/agent/tool-guard/${requestId}/resolve`, { approved });
}

// ── Task Cancel API ────────────────────────────────────────

export async function cancelTask(taskId: string, reason?: string): Promise<{ task_id: string; status: string; message: string }> {
  const { data } = await api.post(`/tasks/${taskId}/cancel`, { reason });
  return data;
}

// ── Scheduler API ────────────────────────────────────────

export interface AgentProcessInfo {
  process_id: string;
  agent_type: "pa" | "sub_agent" | "cron";
  state: "pending" | "starting" | "running" | "idle" | "stopping" | "stopped" | "failed";
  session_id: string | null;
  task_id: string | null;
  agent_name: string | null;
  os_pid: number | null;
  started_at: string;
  last_active_at: string;
  idle_seconds: number;
  error: string | null;
}

export interface SchedulerStats {
  total_processes: number;
  pa_count: number;
  sub_agent_count: number;
  cron_count: number;
  by_state: Record<string, number>;
  total_memory_mb: number;
  uptime_seconds: number;
}

export async function getSchedulerAgents(): Promise<AgentProcessInfo[]> {
  const { data } = await api.get<AgentProcessInfo[]>("/scheduler/agents");
  return data;
}

export async function getSchedulerStats(): Promise<SchedulerStats> {
  const { data } = await api.get<SchedulerStats>("/scheduler/stats");
  return data;
}

export async function stopSchedulerAgent(id: string): Promise<void> {
  await api.post(`/scheduler/agents/${encodeURIComponent(id)}/stop`);
}

export default api;
