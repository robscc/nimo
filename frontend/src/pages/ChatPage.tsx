import { useState, useRef, useEffect, useCallback } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  Send, Trash2, ChevronDown, ChevronRight,
  Loader2, XCircle, Paperclip,
  Settings, Smartphone,
  CalendarClock, ImagePlus,
} from "lucide-react";
import clsx from "clsx";
import { clearMemory, createSession, getSessions, getSessionMessages, resolveToolGuard } from "../api";
import NimoIcon from "../components/NimoIcon";
import SessionPanel from "../components/SessionPanel";
import MentionPopup from "../components/MentionPopup";
import { useSessionMeta } from "../hooks/useSessionMeta";
import { useSubAgents } from "../hooks/useSubAgents";
import { useSessionEvents } from "../hooks/useSessionEvents";

import type { Message, PlanData } from "../types/chat";
import { mapHistoryToMessages } from "../utils/chatHelpers";
import {
  ThinkingBubble,
  ToolGuardCard,
  ToolCallCard,
  TaskResultCard,
  PlanCard,
  SessionMetaPanel,
} from "../components/chat";

// ── Main Page ──────────────────────────────────────────────

export default function ChatPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const sessionIdParam = searchParams.get("session");
  const [sessionId, setSessionId] = useState<string | null>(sessionIdParam);
  const [panelCollapsed, setPanelCollapsed] = useState(false);
  const [showMeta, setShowMeta] = useState(false);

  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [pendingImages, setPendingImages] = useState<string[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const chatInputRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();

  const isReadOnly = sessionId?.startsWith("dingtalk:") ?? false;

  // ── @mention 自动提示 ─────────────────────────────────────
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionQuery, setMentionQuery] = useState("");
  const [mentionIndex, setMentionIndex] = useState(0);

  const { data: sessionMeta } = useSessionMeta(sessionId);
  const subAgentMode = sessionMeta?.sub_agent_mode ?? null;
  // sub_agent_mode 为 "off" 时禁用 @mention
  const mentionEnabled = subAgentMode !== "off";

  const { data: subAgents } = useSubAgents();
  const enabledAgents = mentionEnabled
    ? (subAgents ?? []).filter((a) => a.enabled)
    : [];
  const filteredAgents = enabledAgents.filter(
    (a) =>
      !mentionQuery ||
      a.name.toLowerCase().includes(mentionQuery.toLowerCase()) ||
      a.display_name.toLowerCase().includes(mentionQuery.toLowerCase()),
  );

  // ── 图片处理 ────────────────────────────────────────────
  const addImageFiles = useCallback((files: File[]) => {
    const imageFiles = files.filter((f) => f.type.startsWith("image/"));
    if (imageFiles.length === 0) return;
    // 限制：单张 ≤ 10MB，最多 5 张
    const allowed = imageFiles
      .filter((f) => f.size <= 10 * 1024 * 1024)
      .slice(0, 5 - pendingImages.length);
    for (const file of allowed) {
      const reader = new FileReader();
      reader.onload = () => {
        setPendingImages((prev) =>
          prev.length < 5 ? [...prev, reader.result as string] : prev
        );
      };
      reader.readAsDataURL(file);
    }
  }, [pendingImages.length]);

  const removePendingImage = (index: number) => {
    setPendingImages((prev) => prev.filter((_, i) => i !== index));
  };

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = Array.from(e.clipboardData.items);
    const imageItems = items.filter((item) => item.type.startsWith("image/"));
    if (imageItems.length === 0) return;
    e.preventDefault();
    const files = imageItems
      .map((item) => item.getAsFile())
      .filter(Boolean) as File[];
    addImageFiles(files);
  }, [addImageFiles]);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    const imageFiles = files.filter((f) => f.type.startsWith("image/"));
    const zipFile = files.find((f) => f.name.endsWith(".zip"));
    if (imageFiles.length > 0) {
      addImageFiles(imageFiles);
    } else if (zipFile) {
      // 模拟 zip 上传：创建一个合成的 change event
      const dt = new DataTransfer();
      dt.items.add(zipFile);
      if (fileInputRef.current) {
        fileInputRef.current.files = dt.files;
        fileInputRef.current.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }
  }, [addImageFiles]);

  // Tool Guard resolve handler
  const handleGuardResolve = async (requestId: string, approved: boolean) => {
    try {
      await resolveToolGuard(requestId, approved, sessionId ?? undefined);
    } catch (err) {
      console.error("Failed to resolve tool guard:", err);
    }
  };

  // 实时接收定时任务推送的 session 消息
  const handleRemoteMessage = useCallback(
    (event: { type: "new_message"; message: { id: string; role: string; content: string; created_at: string | null; meta?: Record<string, unknown> } }) => {
      const { message } = event;
      if (message.role !== "assistant" && message.role !== "user") return;
      const meta = message.meta;
      setMessages((prev) => [
        ...prev,
        {
          role: message.role as "user" | "assistant",
          content: message.content,
          cardType: meta?.card_type as string | undefined,
          cardMeta: meta?.card_type ? meta : undefined,
        },
      ]);
    },
    []
  );
  useSessionEvents(sessionId, handleRemoteMessage);

  // 初始化：优先加载 URL 中的 session，否则加载最新 session，否则新建
  useEffect(() => {
    (async () => {
      if (sessionIdParam) {
        // URL 已指定 session：直接加载其消息
        const history = await getSessionMessages(sessionIdParam);
        setMessages(mapHistoryToMessages(history));
      } else {
        // 无 URL 参数：加载最近一条 session，没有才新建
        const sessions = await getSessions();
        if (sessions.length > 0) {
          const latest = sessions[0];
          setSessionId(latest.id);
          setSearchParams({ session: latest.id }, { replace: true });
          const history = await getSessionMessages(latest.id);
          setMessages(mapHistoryToMessages(history));
        } else {
          const { id } = await createSession();
          setSessionId(id);
          setSearchParams({ session: id }, { replace: true });
        }
      }
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // 切换到历史 session
  const handleSelectSession = async (id: string) => {
    abortRef.current?.abort();
    setIsStreaming(false);
    setSessionId(id);
    setSearchParams({ session: id });
    setPendingImages([]);
    const history = await getSessionMessages(id);
    setMessages(mapHistoryToMessages(history));
  };

  // 新建对话回调
  const handleNewSession = (id: string) => {
    abortRef.current?.abort();
    setIsStreaming(false);
    setSessionId(id);
    setMessages([]);
    setPendingImages([]);
    setSearchParams({ session: id });
    queryClient.invalidateQueries({ queryKey: ["sessions"] });
  };

  const sendMessage = async (text: string) => {
    if ((!text.trim() && pendingImages.length === 0) || isStreaming || !sessionId) return;

    const images = pendingImages.length > 0 ? [...pendingImages] : undefined;
    setInput("");
    setPendingImages([]);
    setIsStreaming(true);

    // 一次性追加 user + assistant 占位消息
    setMessages((prev) => [
      ...prev,
      { role: "user", content: text, images },
      { role: "assistant", content: "", toolCalls: [], streaming: true },
    ]);

    abortRef.current = new AbortController();

    try {
      const resp = await fetch("/api/v1/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          message: text,
          channel: "web",
          images,
        }),
        signal: abortRef.current.signal,
      });

      if (!resp.ok || !resp.body) {
        throw new Error(`HTTP ${resp.status}`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";

      const updateLast = (updater: (m: Message) => Message) =>
        setMessages((prev) => prev.map((m, i) => (i === prev.length - 1 ? updater(m) : m)));

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const ev = JSON.parse(line.slice(6)) as Record<string, unknown>;

          if (ev.type === "thinking_delta") {
            updateLast((m) => ({
              ...m,
              thinking: (m.thinking ?? "") + (ev.delta as string),
              streamingThinking: true,
            }));
          } else if (ev.type === "tool_start") {
            updateLast((m) => ({
              ...m,
              toolCalls: [
                ...(m.toolCalls ?? []),
                {
                  id: ev.id as string,
                  name: ev.name as string,
                  input: ev.input as Record<string, unknown>,
                  status: "running" as const,
                },
              ],
            }));
          } else if (ev.type === "tool_done") {
            updateLast((m) => ({
              ...m,
              toolCalls: (m.toolCalls ?? []).map((tc) =>
                tc.id === ev.id
                  ? {
                      ...tc,
                      output: ev.output as string,
                      error: ev.error as string | null,
                      duration_ms: ev.duration_ms as number,
                      status: "done" as const,
                    }
                  : tc,
              ),
            }));
          } else if (ev.type === "text_delta") {
            updateLast((m) => ({
              ...m,
              content: m.content + (ev.delta as string),
              streamingThinking: false,
            }));
          } else if (ev.type === "file") {
            updateLast((m) => ({
              ...m,
              files: [
                ...(m.files ?? []),
                { url: ev.url as string, name: ev.name as string, mime: ev.mime as string },
              ],
            }));
          } else if (ev.type === "tool_guard_request") {
            updateLast((m) => ({
              ...m,
              guardRequest: {
                requestId: ev.request_id as string,
                toolName: ev.tool_name as string,
                toolInput: ev.tool_input as Record<string, unknown>,
                level: ev.level as number,
                rule: (ev.rule as string) || null,
                threshold: ev.threshold as number,
                description: (ev.description as string) || "",
                status: "pending",
              },
            }));
          } else if (ev.type === "tool_guard_waiting") {
            // 心跳，不需特殊处理
          } else if (ev.type === "tool_guard_resolved") {
            updateLast((m) => ({
              ...m,
              guardRequest: m.guardRequest
                ? { ...m.guardRequest, status: (ev.approved as boolean) ? "approved" : "rejected" }
                : undefined,
            }));
          } else if (ev.type === "retry") {
            updateLast((m) => ({
              ...m,
              retries: [
                ...(m.retries ?? []),
                {
                  attempt: ev.attempt as number,
                  maxAttempts: ev.max_attempts as number,
                  error: ev.error as string,
                  delay: ev.delay as number,
                },
              ],
            }));
          } else if (ev.type === "plan_generating") {
            updateLast((m) => ({
              ...m,
              planGenerating: true,
              content: "",
            }));
          } else if (ev.type === "plan_ready") {
            updateLast((m) => ({
              ...m,
              plan: ev.plan as PlanData,
              planGenerating: false,
            }));
          } else if (ev.type === "plan_step_start") {
            updateLast((m) => {
              if (!m.plan) return m;
              const steps = m.plan.steps.map((s) =>
                s.index === (ev.step_index as number) ? { ...s, status: "running" } : s,
              );
              return { ...m, plan: { ...m.plan, status: "executing", steps, current_step: ev.step_index as number } };
            });
          } else if (ev.type === "plan_step_done") {
            updateLast((m) => {
              if (!m.plan) return m;
              const steps = m.plan.steps.map((s) =>
                s.index === (ev.step_index as number)
                  ? { ...s, status: (ev.result as string)?.startsWith("失败") ? "failed" : "completed", result: ev.result as string }
                  : s,
              );
              return { ...m, plan: { ...m.plan, steps } };
            });
          } else if (ev.type === "plan_completed") {
            updateLast((m) => {
              if (!m.plan) return m;
              const updated = ev.plan as PlanData;
              return { ...m, plan: { ...m.plan, ...updated, status: "completed" } };
            });
          } else if (ev.type === "plan_cancelled") {
            updateLast((m) => {
              if (!m.plan) return m;
              return { ...m, plan: { ...m.plan, status: "cancelled" } };
            });
          } else if (ev.type === "done") {
            updateLast((m) => ({ ...m, streaming: false }));
            // 刷新会话列表（更新 title 和 message_count）
            queryClient.invalidateQueries({ queryKey: ["sessions"] });
          } else if (ev.type === "error") {
            updateLast((m) => ({
              ...m,
              content: `\u26a0\ufe0f ${ev.message as string}`,
              streaming: false,
            }));
          }
        }
      }
    } catch (err) {
      if (err instanceof Error && err.name !== "AbortError") {
        setMessages((prev) =>
          prev.map((m, i) =>
            i === prev.length - 1
              ? { ...m, content: "\u26a0\ufe0f 请求失败，请检查网络或稍后重试", streaming: false }
              : m,
          ),
        );
      }
    } finally {
      setIsStreaming(false);
    }
  };

  // ── @mention 输入检测 ──────────────────────────────────────
  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const val = e.target.value;
    setInput(val);

    // 光标前的文本
    const cursorPos = e.target.selectionStart ?? val.length;
    const beforeCursor = val.slice(0, cursorPos);

    // 匹配: 行首或空白后的 @xxx（避免邮箱误触发）
    const match = beforeCursor.match(/(^|[\s])@(\S*)$/);
    if (match) {
      setMentionQuery(match[2]);
      setMentionOpen(true);
      setMentionIndex(0);
    } else {
      setMentionOpen(false);
      setMentionQuery("");
    }
  };

  const handleMentionSelect = (agent: { name: string; display_name: string }) => {
    const el = chatInputRef.current;
    if (!el) return;

    const cursorPos = el.selectionStart ?? input.length;
    const beforeCursor = input.slice(0, cursorPos);
    const afterCursor = input.slice(cursorPos);

    // 找到最后一个 @（触发 mention 的位置）
    const atIndex = beforeCursor.lastIndexOf("@");
    if (atIndex === -1) return;

    const replacement = `@${agent.display_name} `;
    const newValue = beforeCursor.slice(0, atIndex) + replacement + afterCursor;
    setInput(newValue);
    setMentionOpen(false);
    setMentionQuery("");

    // 恢复焦点并设置光标
    requestAnimationFrame(() => {
      el.focus();
      const newCursor = atIndex + replacement.length;
      el.setSelectionRange(newCursor, newCursor);
    });
  };

  const handleInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (!mentionOpen || filteredAgents.length === 0) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setMentionIndex((prev) => (prev + 1) % filteredAgents.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setMentionIndex((prev) => (prev - 1 + filteredAgents.length) % filteredAgents.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      handleMentionSelect(filteredAgents[mentionIndex]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setMentionOpen(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    sendMessage(input.trim());
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    // 图片文件 → 加入待发送列表
    if (file.type.startsWith("image/")) {
      addImageFiles([file]);
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }

    // zip 文件 → 走技能包安装流程
    if (!file.name.endsWith(".zip")) return;

    setMessages((prev) => [
      ...prev,
      { role: "user", content: `📦 上传技能包: ${file.name}` },
      { role: "assistant", content: "", toolCalls: [], streaming: true },
    ]);
    setIsStreaming(true);

    try {
      const form = new FormData();
      form.append("file", file);
      const resp = await fetch("/api/v1/skills/install/zip", {
        method: "POST",
        body: form,
      });
      const data = await resp.json();

      if (resp.ok) {
        setMessages((prev) =>
          prev.map((m, i) =>
            i === prev.length - 1
              ? {
                  ...m,
                  content: `✅ 技能 **${data.name}** v${data.version} 安装成功！\n\n包含 ${data.tools.length} 个工具: ${data.tools.join(", ")}`,
                  streaming: false,
                }
              : m,
          ),
        );
      } else {
        setMessages((prev) =>
          prev.map((m, i) =>
            i === prev.length - 1
              ? { ...m, content: `⚠️ 安装失败: ${data.detail || "未知错误"}`, streaming: false }
              : m,
          ),
        );
      }
    } catch {
      setMessages((prev) =>
        prev.map((m, i) =>
          i === prev.length - 1
            ? { ...m, content: "⚠️ 上传失败，请检查网络", streaming: false }
            : m,
        ),
      );
    } finally {
      setIsStreaming(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  // 清空当前对话并创建新 session
  const handleClear = async () => {
    abortRef.current?.abort();
    if (sessionId) await clearMemory(sessionId);
    const { id } = await createSession();
    setSessionId(id);
    setMessages([]);
    setPendingImages([]);
    setSearchParams({ session: id });
    queryClient.invalidateQueries({ queryKey: ["sessions"] });
  };

  return (
    <div className="flex h-full">
      {/* Session Panel */}
      <SessionPanel
        currentSessionId={sessionId}
        onSelectSession={handleSelectSession}
        onNewSession={handleNewSession}
        collapsed={panelCollapsed}
        onToggleCollapse={() => setPanelCollapsed((v) => !v)}
      />

      {/* Chat Area */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-3 bg-white border-b">
          <h1 className="font-semibold text-gray-800 flex items-center gap-2">
            <NimoIcon size={20} />
            <span>nimo</span>
          </h1>
          <div className="flex items-center gap-1">
          {!isReadOnly && (
          <button
            onClick={handleClear}
            className="p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
            title="清空对话"
          >
            <Trash2 size={18} />
          </button>
          )}
          {!isReadOnly && sessionId && (
          <Link
            to={`/cron?from_session=${encodeURIComponent(sessionId)}`}
            className="p-2 text-gray-400 hover:text-nimo-500 hover:bg-nimo-50 rounded-lg transition-colors"
            title="新建定时任务（通知到此会话）"
          >
            <CalendarClock size={18} />
          </Link>
          )}
          {!isReadOnly && (
          <button
            onClick={() => setShowMeta((v) => !v)}
            className={clsx(
              "p-2 rounded-lg transition-colors",
              showMeta
                ? "text-nimo-500 bg-nimo-50"
                : "text-gray-400 hover:text-gray-600 hover:bg-gray-100"
            )}
            title="会话信息"
          >
            <Settings size={18} />
          </button>
          )}
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-gray-400 gap-4">
              <NimoIcon size={64} />
              <p className="text-lg text-gray-500">嗨！我是 nimo，你的智能助手 🐠</p>
            </div>
          )}

          {messages.map((msg, i) => (
            msg.isContextSummary ? (
              /* ── 摘要分隔线卡片 ─────────────────────── */
              <div key={i} className="flex justify-center my-3">
                <div className="max-w-[80%] w-full rounded-lg border border-dashed border-gray-300 bg-gray-50/60 px-4 py-3">
                  <div className="flex items-center gap-2 text-xs text-gray-400 font-medium mb-1.5">
                    <span className="flex-1 border-t border-gray-200" />
                    <span className="shrink-0">📦 以上 {msg.compressedCount ?? "?"} 条已压缩为摘要</span>
                    <span className="flex-1 border-t border-gray-200" />
                  </div>
                  <div className="text-xs text-gray-500 leading-relaxed whitespace-pre-wrap">
                    {msg.content}
                  </div>
                </div>
              </div>
            ) : (
            <div
              key={i}
              className={clsx(
                "flex",
                msg.role === "user" ? "justify-end" : "justify-start",
                msg.compressed && "opacity-50",
              )}
            >
              {msg.role === "assistant" ? (
                <div className="max-w-[75%] space-y-1.5 min-w-0">
                  {/* Thinking bubble */}
                  {msg.thinking && (
                    <ThinkingBubble content={msg.thinking} streaming={msg.streamingThinking} />
                  )}
                  {/* Tool Guard confirmation */}
                  {msg.guardRequest && (
                    <ToolGuardCard guard={msg.guardRequest} onResolve={handleGuardResolve} />
                  )}
                  {/* Tool call cards */}
                  {(msg.toolCalls ?? []).map((tc) => (
                    <ToolCallCard key={tc.id} tc={tc} />
                  ))}
                  {/* 文件附件 */}
                  {(msg.files ?? []).map((f, fi) =>
                    f.mime.startsWith("image/") ? (
                      <img
                        key={fi}
                        src={f.url}
                        alt={f.name}
                        className="max-w-full rounded-xl border shadow-sm max-h-80 object-contain"
                      />
                    ) : (
                      <a
                        key={fi}
                        href={f.url}
                        download={f.name}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-center gap-2 px-3 py-2 rounded-lg border bg-white text-sm text-nimo-600 hover:bg-nimo-50 transition-colors"
                      >
                        <Paperclip size={14} /> {f.name}
                      </a>
                    )
                  )}
                  {/* Retry status */}
                  {(msg.retries ?? []).length > 0 && (
                    <div className="rounded-lg border border-amber-200 bg-amber-50/80 text-xs overflow-hidden">
                      <div className="flex items-center gap-1.5 px-3 py-1.5 text-amber-700 font-medium">
                        <Loader2 size={12} className={msg.streaming ? "animate-spin" : ""} />
                        <span>LLM 调用重试</span>
                      </div>
                      <div className="px-3 pb-2 space-y-0.5">
                        {(msg.retries ?? []).map((r, ri) => (
                          <div key={ri} className="flex items-center gap-1.5 text-amber-600">
                            <XCircle size={10} className="shrink-0 text-amber-500" />
                            <span>
                              第 {r.attempt}/{r.maxAttempts} 次失败：{r.error}
                              {msg.streaming && ri === (msg.retries?.length ?? 0) - 1
                                ? `，${r.delay}s 后重试…`
                                : ""}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                  {/* Plan Card — priority over text bubble */}
                  {(msg.plan || msg.planGenerating) && (
                    <PlanCard plan={msg.plan} generating={msg.planGenerating} />
                  )}
                  {/* Text bubble — show if has content OR still streaming (and no plan) */}
                  {!msg.plan && !msg.planGenerating && (msg.content || msg.streaming) && (
                    msg.cardType ? (
                      <TaskResultCard msg={msg} />
                    ) : (
                    <div data-testid="assistant-message" className="bg-white border rounded-2xl px-4 py-2.5 text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">
                      {msg.content || (
                        <span className="text-gray-400 animate-pulse">思考中…</span>
                      )}
                      {/* Blinking cursor */}
                      {msg.streaming && msg.content && (
                        <span className="inline-block w-0.5 h-[1em] bg-nimo-500 ml-0.5 align-middle animate-pulse" />
                      )}
                    </div>
                    )
                  )}
                </div>
              ) : (
                <div data-testid="user-message" className="max-w-[70%] bg-nimo-500 text-white rounded-2xl px-4 py-2.5 text-sm leading-relaxed">
                  {msg.images && msg.images.length > 0 && (
                    <div className="flex gap-2 mb-2 flex-wrap">
                      {msg.images.map((img, imgIdx) => (
                        <img
                          key={imgIdx}
                          src={img}
                          alt={`附图 ${imgIdx + 1}`}
                          className="max-h-40 rounded-lg cursor-pointer hover:opacity-90 transition-opacity"
                          onClick={() => window.open(img, "_blank")}
                        />
                      ))}
                    </div>
                  )}
                  {msg.content}
                </div>
              )}
            </div>
            )
          ))}

          <div ref={bottomRef} />
        </div>

        {/* Input */}
        {isReadOnly ? (
          <div className="px-6 py-4 bg-white border-t flex items-center justify-center gap-2 text-gray-400 text-sm">
            <Smartphone size={16} className="text-blue-400" />
            <span>钉钉会话（只读）— 仅供查看历史消息</span>
          </div>
        ) : (
        <form
          onSubmit={handleSubmit}
          onPaste={handlePaste}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={clsx(
            "px-6 py-4 bg-white border-t transition-colors",
            dragOver && "bg-nimo-50 border-nimo-300",
          )}
        >
          {/* 图片预览区域 */}
          {pendingImages.length > 0 && (
            <div className="flex gap-2 mb-3 overflow-x-auto pb-1">
              {pendingImages.map((img, i) => (
                <div key={i} className="relative shrink-0 group">
                  <img
                    src={img}
                    alt={`预览 ${i + 1}`}
                    className="h-16 w-16 object-cover rounded-lg border border-gray-200 shadow-sm"
                  />
                  <button
                    type="button"
                    onClick={() => removePendingImage(i)}
                    className="absolute -top-1.5 -right-1.5 bg-red-500 text-white rounded-full w-4 h-4 text-[10px] flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity shadow"
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}
          {/* 拖拽提示 */}
          {dragOver && (
            <div className="mb-3 flex items-center justify-center gap-2 py-3 rounded-xl border-2 border-dashed border-nimo-300 bg-nimo-50/50 text-nimo-500 text-sm">
              <ImagePlus size={18} />
              释放以添加图片
            </div>
          )}
          <div className="flex gap-3 relative">
          {/* @mention 弹窗 */}
          {mentionOpen && filteredAgents.length > 0 && (
            <MentionPopup
              agents={filteredAgents}
              selectedIndex={mentionIndex}
              onSelect={handleMentionSelect}
            />
          )}
          <label
            className="w-10 h-10 rounded-xl border border-gray-200 text-gray-400 hover:text-nimo-500 hover:border-nimo-300 flex items-center justify-center cursor-pointer transition-colors shrink-0"
            title="上传图片或技能包 (.zip)"
          >
            <Paperclip size={18} />
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip,image/*"
              className="hidden"
              onChange={handleFileUpload}
              disabled={isStreaming}
            />
          </label>
          <input
            ref={chatInputRef}
            data-testid="chat-input"
            value={input}
            onChange={handleInputChange}
            onKeyDown={handleInputKeyDown}
            onBlur={() => setTimeout(() => setMentionOpen(false), 150)}
            placeholder={pendingImages.length > 0 ? "添加描述文字...（可选）" : "输入消息...（@ 可选择 SubAgent）"}
            className="flex-1 rounded-xl border border-gray-200 px-4 py-2 text-sm outline-none focus:border-nimo-400 transition-colors"
            disabled={isStreaming}
          />
          <button
            data-testid="chat-submit"
            type="submit"
            disabled={isStreaming || (!input.trim() && pendingImages.length === 0)}
            className="w-10 h-10 rounded-xl bg-nimo-500 text-white flex items-center justify-center hover:bg-nimo-600 disabled:opacity-40 transition-colors"
          >
            <Send size={18} />
          </button>
          </div>
        </form>
        )}
      </div>

      {/* Session Meta Panel */}
      {showMeta && sessionId && !isReadOnly && (
        <SessionMetaPanel
          sessionId={sessionId}
          onClose={() => setShowMeta(false)}
        />
      )}
    </div>
  );
}
