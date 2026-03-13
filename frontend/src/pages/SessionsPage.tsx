import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import {
  MessageSquare, Trash2, Search, Cpu, Clock, Hash,
  Loader2, MessagesSquare, ChevronDown, ChevronRight, ExternalLink,
} from "lucide-react";
import clsx from "clsx";
import { useSessions } from "../hooks/useSessions";
import { deleteSession, type SessionSummary } from "../api";

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const min = Math.floor(diff / 60000);
  if (min < 1) return "刚刚";
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day} 天前`;
  return new Date(iso).toLocaleDateString("zh-CN");
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function SessionRow({
  session,
  onOpen,
  onDelete,
  deleting,
}: {
  session: SessionSummary;
  onOpen: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className={clsx(
      "border rounded-xl bg-white transition-shadow",
      expanded ? "shadow-sm ring-1 ring-nimo-100" : "hover:shadow-sm"
    )}>
      {/* Main row */}
      <div className="flex items-center gap-3 px-4 py-3">
        {/* Expand toggle */}
        <button
          onClick={() => setExpanded((v) => !v)}
          className="w-6 h-6 flex items-center justify-center rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100 shrink-0"
        >
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>

        {/* Icon */}
        <div className="w-9 h-9 rounded-lg bg-nimo-50 flex items-center justify-center shrink-0">
          <MessageSquare size={16} className="text-nimo-500" />
        </div>

        {/* Title + time */}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-800 truncate">{session.title}</p>
          <p className="text-xs text-gray-400 mt-0.5">{relativeTime(session.updated_at)}</p>
        </div>

        {/* Model badge */}
        <span className="hidden sm:inline-flex items-center gap-1 text-[11px] font-mono px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 shrink-0">
          <Cpu size={10} />
          {session.model_name || "default"}
        </span>

        {/* Message count */}
        <span className="inline-flex items-center gap-1 text-xs text-gray-400 shrink-0 w-16 justify-end">
          <Hash size={11} />
          {session.message_count}
        </span>

        {/* Actions */}
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={onOpen}
            className="p-1.5 rounded-lg text-gray-400 hover:text-nimo-600 hover:bg-nimo-50 transition-colors"
            title="打开对话"
          >
            <ExternalLink size={14} />
          </button>
          <button
            onClick={onDelete}
            disabled={deleting}
            className="p-1.5 rounded-lg text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors disabled:opacity-40"
            title="删除会话"
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="px-4 pb-3 pt-1 border-t mx-4 mt-0">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3">
            <div className="space-y-0.5">
              <p className="text-[10px] font-medium text-gray-400 uppercase tracking-wider">模型</p>
              <p className="text-xs font-mono text-gray-700">{session.model_name || "默认"}</p>
            </div>
            <div className="space-y-0.5">
              <p className="text-[10px] font-medium text-gray-400 uppercase tracking-wider">渠道</p>
              <p className="text-xs text-gray-700">{session.channel}</p>
            </div>
            <div className="space-y-0.5">
              <p className="text-[10px] font-medium text-gray-400 uppercase tracking-wider">创建时间</p>
              <p className="text-xs text-gray-700">{formatDate(session.created_at)}</p>
            </div>
            <div className="space-y-0.5">
              <p className="text-[10px] font-medium text-gray-400 uppercase tracking-wider">最后活跃</p>
              <p className="text-xs text-gray-700">{formatDate(session.updated_at)}</p>
            </div>
          </div>
          <div className="mt-3">
            <p className="text-[10px] font-medium text-gray-400 uppercase tracking-wider">Session ID</p>
            <p className="text-xs font-mono text-gray-500 mt-0.5 select-all">{session.id}</p>
          </div>
        </div>
      )}
    </div>
  );
}

export default function SessionsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: sessions = [], isLoading } = useSessions();
  const [search, setSearch] = useState("");
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const filtered = sessions.filter((s) =>
    !search || s.title.toLowerCase().includes(search.toLowerCase()) || s.id.includes(search)
  );

  const handleOpen = (id: string) => {
    navigate(`/chat?session=${id}`);
  };

  const handleDelete = async (id: string) => {
    setDeletingId(id);
    try {
      await deleteSession(id);
      queryClient.invalidateQueries({ queryKey: ["sessions"] });
    } finally {
      setDeletingId(null);
    }
  };

  const totalMessages = sessions.reduce((sum, s) => sum + s.message_count, 0);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 bg-white border-b">
        <div className="flex items-center justify-between mb-3">
          <h1 className="font-semibold text-gray-800 flex items-center gap-2">
            <MessagesSquare size={18} className="text-nimo-500" />
            会话管理
          </h1>
          <div className="flex items-center gap-3 text-xs text-gray-400">
            <span className="flex items-center gap-1">
              <MessageSquare size={12} /> {sessions.length} 个会话
            </span>
            <span className="flex items-center gap-1">
              <Hash size={12} /> {totalMessages} 条消息
            </span>
          </div>
        </div>

        {/* Search */}
        <div className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索会话标题或 ID ..."
            className="w-full pl-9 pr-4 py-2 rounded-lg border border-gray-200 text-sm outline-none focus:border-nimo-400 transition-colors bg-gray-50"
          />
        </div>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {isLoading ? (
          <div className="flex items-center justify-center py-20">
            <Loader2 size={24} className="animate-spin text-gray-300" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 text-gray-400 gap-3">
            <MessagesSquare size={40} className="text-gray-200" />
            <p className="text-sm">{search ? "没有匹配的会话" : "暂无会话"}</p>
          </div>
        ) : (
          <div className="space-y-2">
            {filtered.map((session) => (
              <SessionRow
                key={session.id}
                session={session}
                onOpen={() => handleOpen(session.id)}
                onDelete={() => handleDelete(session.id)}
                deleting={deletingId === session.id}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
