/**
 * NotificationProvider — 全局 WebSocket 通知订阅。
 *
 * 在 QueryClientProvider 内、App 外包裹，确保 useQueryClient 可用。
 * 仅调用 useNotifications() 副作用，不添加额外 DOM 节点。
 */

import type { ReactNode } from "react";
import { useNotifications } from "../hooks/useNotifications";

export function NotificationProvider({ children }: { children: ReactNode }) {
  useNotifications();
  return <>{children}</>;
}
