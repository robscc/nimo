/**
 * useNotifications — WebSocket 通知订阅 Hook。
 *
 * 连接后端 /api/v1/notifications/ws，收到推送后按类型
 * invalidate 对应的 React Query 缓存。
 *
 * 纯副作用 Hook，不返回 state。
 */

import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";

// ── 通知类型到 React Query Key 的映射 ──────────────────────

const INVALIDATION_MAP: Record<string, string[][]> = {
  subagent_task_done: [["tasks"], ["dashboard-stats"]],
  subagent_task_failed: [["tasks"], ["dashboard-stats"]],
  cron_execution_done: [["cron-executions"], ["cron-jobs"], ["dashboard-stats"]],
  cron_execution_failed: [
    ["cron-executions"],
    ["cron-jobs"],
    ["dashboard-stats"],
  ],
};

// ── 退避计算 ──────────────────────────────────────────────

const BACKOFF_BASE = 1000; // 1 秒
const BACKOFF_MAX = 30_000; // 30 秒

function getBackoff(attempt: number): number {
  return Math.min(BACKOFF_BASE * 2 ** attempt, BACKOFF_MAX);
}

// ── 构建 WebSocket URL ────────────────────────────────────

function buildWsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api/v1/notifications/ws`;
}

// ── Hook ──────────────────────────────────────────────────

export function useNotifications(): void {
  const queryClient = useQueryClient();
  const attemptRef = useRef(0);
  const wsRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let unmounted = false;

    function connect() {
      if (unmounted) return;

      const ws = new WebSocket(buildWsUrl());
      wsRef.current = ws;

      ws.onopen = () => {
        attemptRef.current = 0; // 连接成功，重置退避
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === "ping") return; // 忽略心跳

          const keys = INVALIDATION_MAP[data.type];
          if (keys) {
            for (const queryKey of keys) {
              queryClient.invalidateQueries({ queryKey });
            }
          }
        } catch {
          // 忽略解析错误
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        if (unmounted) return;

        // 指数退避重连
        const delay = getBackoff(attemptRef.current);
        attemptRef.current += 1;
        timerRef.current = setTimeout(connect, delay);
      };

      ws.onerror = () => {
        // onerror 之后会触发 onclose，在 onclose 中处理重连
      };
    }

    connect();

    return () => {
      unmounted = true;
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [queryClient]);
}
