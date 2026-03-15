/**
 * useSessionEvents — 订阅指定 session 的实时消息推送（SSE）。
 *
 * 当定时任务完成并向 session 写入结果时，此 hook 会触发 onNewMessage 回调。
 * 网络断开后每 3 秒自动重连。
 */
import { useEffect, useRef } from "react";

export interface SessionNewMessageEvent {
  type: "new_message";
  message: {
    id: string;
    role: string;
    content: string;
    created_at: string | null;
  };
}

export type SessionEvent = SessionNewMessageEvent | { type: "connected"; session_id: string };

export function useSessionEvents(
  sessionId: string | null,
  onNewMessage?: (event: SessionNewMessageEvent) => void
) {
  const onNewMessageRef = useRef(onNewMessage);
  onNewMessageRef.current = onNewMessage;

  useEffect(() => {
    if (!sessionId) return;

    let es: EventSource | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      es = new EventSource(`/api/v1/sessions/${sessionId}/events`);

      es.onmessage = (e) => {
        try {
          const event: SessionEvent = JSON.parse(e.data);
          if (event.type === "new_message") {
            onNewMessageRef.current?.(event);
          }
        } catch {
          // 忽略解析错误
        }
      };

      es.onerror = () => {
        es?.close();
        es = null;
        if (!cancelled) {
          retryTimer = setTimeout(connect, 3000);
        }
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      es?.close();
    };
  }, [sessionId]);
}
