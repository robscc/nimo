import { useState, useRef, useEffect } from "react";
import {
  Send, Trash2, Wrench, ChevronDown, ChevronRight,
  CheckCircle2, Loader2, XCircle,
} from "lucide-react";
import clsx from "clsx";
import { clearMemory } from "../api";

// ── Types ─────────────────────────────────────────────────

interface ToolCallEntry {
  id: string;
  name: string;
  input: Record<string, unknown>;
  output?: string;
  error?: string | null;
  duration_ms?: number;
  status: "running" | "done";
}

interface Message {
  role: "user" | "assistant";
  content: string;
  toolCalls?: ToolCallEntry[];
  streaming?: boolean;
}

const SESSION_ID = `web:${crypto.randomUUID()}`;

// ── Tool Call Card ─────────────────────────────────────────

function ToolCallCard({ tc }: { tc: ToolCallEntry }) {
  const [open, setOpen] = useState(false);
  const hasError = !!tc.error;

  return (
    <div className={clsx(
      "rounded-lg border text-xs overflow-hidden",
      hasError ? "border-red-200 bg-red-50/60" : "border-indigo-100 bg-indigo-50/50",
    )}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-black/5 transition-colors"
      >
        {tc.status === "running" ? (
          <Loader2 size={12} className="text-indigo-500 animate-spin shrink-0" />
        ) : hasError ? (
          <XCircle size={12} className="text-red-400 shrink-0" />
        ) : (
          <CheckCircle2 size={12} className="text-green-500 shrink-0" />
        )}
        <Wrench size={12} className={clsx("shrink-0", hasError ? "text-red-400" : "text-indigo-400")} />
        <span className={clsx("font-mono font-medium", hasError ? "text-red-700" : "text-indigo-700")}>
          {tc.name}
        </span>
        {tc.status === "running" ? (
          <span className="text-indigo-400 text-xs">执行中…</span>
        ) : (
          <span className="text-gray-400 truncate flex-1 text-left ml-1">
            {JSON.stringify(tc.input).slice(0, 60)}
          </span>
        )}
        {tc.duration_ms !== undefined && (
          <span className="text-gray-400 shrink-0 ml-auto">{tc.duration_ms}ms</span>
        )}
        {open
          ? <ChevronDown size={12} className="text-gray-400 shrink-0" />
          : <ChevronRight size={12} className="text-gray-400 shrink-0" />}
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-2 border-t border-black/5">
          <div className="pt-2">
            <p className="text-gray-500 mb-1 font-medium">输入参数</p>
            <pre className="bg-white rounded p-2 border text-gray-700 overflow-x-auto whitespace-pre-wrap">
              {JSON.stringify(tc.input, null, 2)}
            </pre>
          </div>
          {tc.output && (
            <div>
              <p className={clsx("mb-1 font-medium", hasError ? "text-red-500" : "text-gray-500")}>
                {hasError ? "错误" : "输出结果"}
              </p>
              <pre className={clsx(
                "rounded p-2 border overflow-x-auto max-h-48 whitespace-pre-wrap text-xs",
                hasError ? "bg-red-50 border-red-100 text-red-600" : "bg-white text-gray-700",
              )}>
                {tc.output}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = async (text: string) => {
    if (!text.trim() || isStreaming) return;
    setInput("");
    setIsStreaming(true);

    // 一次性追加 user + assistant 占位消息
    setMessages((prev) => [
      ...prev,
      { role: "user", content: text },
      { role: "assistant", content: "", toolCalls: [], streaming: true },
    ]);

    abortRef.current = new AbortController();

    try {
      const resp = await fetch("/api/v1/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: SESSION_ID, message: text, channel: "web" }),
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

          if (ev.type === "tool_start") {
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
            updateLast((m) => ({ ...m, content: m.content + (ev.delta as string) }));
          } else if (ev.type === "done") {
            updateLast((m) => ({ ...m, streaming: false }));
          } else if (ev.type === "error") {
            updateLast((m) => ({
              ...m,
              content: `⚠️ ${ev.message as string}`,
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
              ? { ...m, content: "⚠️ 请求失败，请检查网络或稍后重试", streaming: false }
              : m,
          ),
        );
      }
    } finally {
      setIsStreaming(false);
    }
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    sendMessage(input.trim());
  };

  const handleClear = async () => {
    abortRef.current?.abort();
    await clearMemory(SESSION_ID);
    setMessages([]);
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-3 bg-white border-b">
        <h1 className="font-semibold text-gray-800">AgentPal 助手</h1>
        <button
          onClick={handleClear}
          className="p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors"
          title="清空对话"
        >
          <Trash2 size={18} />
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full text-gray-400">
            <p>你好！有什么我可以帮你的？</p>
          </div>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            {msg.role === "assistant" ? (
              <div className="max-w-[75%] space-y-1.5 min-w-0">
                {/* Tool call cards */}
                {(msg.toolCalls ?? []).map((tc) => (
                  <ToolCallCard key={tc.id} tc={tc} />
                ))}
                {/* Text bubble — show if has content OR still streaming */}
                {(msg.content || msg.streaming) && (
                  <div className="bg-white border rounded-2xl px-4 py-2.5 text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">
                    {msg.content || (
                      <span className="text-gray-400 animate-pulse">思考中…</span>
                    )}
                    {/* Blinking cursor */}
                    {msg.streaming && msg.content && (
                      <span className="inline-block w-0.5 h-[1em] bg-indigo-500 ml-0.5 align-middle animate-pulse" />
                    )}
                  </div>
                )}
              </div>
            ) : (
              <div className="max-w-[70%] bg-indigo-600 text-white rounded-2xl px-4 py-2.5 text-sm leading-relaxed">
                {msg.content}
              </div>
            )}
          </div>
        ))}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <form onSubmit={handleSubmit} className="px-6 py-4 bg-white border-t flex gap-3">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="输入消息..."
          className="flex-1 rounded-xl border border-gray-200 px-4 py-2 text-sm outline-none focus:border-indigo-400 transition-colors"
          disabled={isStreaming}
        />
        <button
          type="submit"
          disabled={isStreaming || !input.trim()}
          className="w-10 h-10 rounded-xl bg-indigo-600 text-white flex items-center justify-center hover:bg-indigo-700 disabled:opacity-40 transition-colors"
        >
          <Send size={18} />
        </button>
      </form>
    </div>
  );
}
