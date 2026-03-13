import { useState } from "react";
import { ChevronLeft, ChevronRight, MessageSquarePlus, Trash2 } from "lucide-react";
import clsx from "clsx";
import { deleteSession } from "../api";
import { useAllSessions, useCreateSession } from "../hooks/useSessions";
import { useQueryClient } from "@tanstack/react-query";
import type { SessionSummary } from "../api";

interface SessionPanelProps {
  currentSessionId: string | null;
  onSelectSession: (id: string) => void;
  onNewSession: (id: string) => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}

interface SessionItemProps {
  session: SessionSummary;
  isActive: boolean;
  isDingtalk: boolean;
  onSelect: () => void;
  onDelete: () => void;
}

function SessionItem({ session, isActive, isDingtalk, onSelect, onDelete }: SessionItemProps) {
  const [hovered, setHovered] = useState(false);

  return (
    <div
      className={clsx(
        "group relative flex items-center gap-2 px-3 py-2.5 rounded-lg cursor-pointer transition-colors",
        isActive
          ? "bg-nimo-100 text-nimo-700"
          : "text-gray-600 hover:bg-gray-100"
      )}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={onSelect}
    >
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium truncate leading-tight">
          {session.title}
          {isDingtalk && (
            <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-blue-100 text-blue-600">
              钉钉
            </span>
          )}
        </p>
        <p className="text-xs text-gray-400 mt-0.5">{session.message_count} 条消息</p>
      </div>

      {hovered && !isDingtalk && (
        <button
          className="shrink-0 p-1 rounded hover:bg-red-100 hover:text-red-500 text-gray-400 transition-colors"
          title="清空对话"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
        >
          <Trash2 size={13} />
        </button>
      )}
    </div>
  );
}

export default function SessionPanel({
  currentSessionId,
  onSelectSession,
  onNewSession,
  collapsed,
  onToggleCollapse,
}: SessionPanelProps) {
  const { data: sessions = [] } = useAllSessions();
  const createSession = useCreateSession();
  const qc = useQueryClient();

  const handleNew = () => {
    createSession.mutate("web", {
      onSuccess: ({ id }) => onNewSession(id),
    });
  };

  const handleDelete = async (sessionId: string) => {
    await deleteSession(sessionId);
    qc.invalidateQueries({ queryKey: ["sessions"] });

    if (sessionId === currentSessionId) {
      // 找下一个 session，否则新建
      const next = sessions.find((s) => s.id !== sessionId);
      if (next) {
        onSelectSession(next.id);
      } else {
        createSession.mutate("web", {
          onSuccess: ({ id }) => onNewSession(id),
        });
      }
    }
  };

  if (collapsed) {
    return (
      <div className="w-10 bg-white border-r flex flex-col items-center pt-3">
        <button
          onClick={onToggleCollapse}
          className="w-8 h-8 flex items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 transition-colors"
          title="展开历史对话"
        >
          <ChevronRight size={16} />
        </button>
      </div>
    );
  }

  return (
    <div className="w-64 bg-white border-r flex flex-col shrink-0">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-3 border-b">
        <span className="text-sm font-semibold text-gray-700">历史对话</span>
        <div className="flex items-center gap-1">
          <button
            onClick={handleNew}
            disabled={createSession.isPending}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-gray-400 hover:bg-nimo-50 hover:text-nimo-500 transition-colors"
            title="新建对话"
          >
            <MessageSquarePlus size={15} />
          </button>
          <button
            onClick={onToggleCollapse}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-gray-400 hover:bg-gray-100 transition-colors"
            title="折叠面板"
          >
            <ChevronLeft size={15} />
          </button>
        </div>
      </div>

      {/* Session list */}
      <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
        {sessions.length === 0 ? (
          <p className="text-xs text-gray-400 text-center py-6">暂无历史对话</p>
        ) : (
          sessions.map((session) => (
            <SessionItem
              key={session.id}
              session={session}
              isActive={session.id === currentSessionId}
              isDingtalk={session.channel === "dingtalk"}
              onSelect={() => onSelectSession(session.id)}
              onDelete={() => handleDelete(session.id)}
            />
          ))
        )}
      </div>
    </div>
  );
}
