import { useEffect, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api";

export interface SkillInfo {
  name: string;
  version: string;
  description: string;
  author: string;
  source: string;
  source_url: string | null;
  enabled: boolean;
  tools: string[];
  skill_type: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface InstallResult {
  name: string;
  version: string;
  description: string;
  tools: string[];
  install_path: string;
  skill_type: string | null;
}

export interface VersionInfo {
  index: number;
  version: string;
  backed_up_at: string | null;
}

export interface SkillReloadedEvent {
  type: "skill_reloaded";
  name: string;
  version: string;
  action: "install" | "rollback" | "uninstall";
}

// ── 基础 CRUD ─────────────────────────────────────────────

export function useSkills() {
  return useQuery<SkillInfo[]>({
    queryKey: ["skills"],
    queryFn: async () => (await api.get("/skills")).data,
    staleTime: 5000,
  });
}

export function useToggleSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      api.patch(`/skills/${name}`, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["skills"] }),
  });
}

export function useInstallSkillFromZip() {
  const qc = useQueryClient();
  return useMutation<InstallResult, Error, File>({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      const { data } = await api.post("/skills/install/zip", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["skills"] }),
  });
}

export function useInstallSkillFromUrl() {
  const qc = useQueryClient();
  return useMutation<InstallResult, Error, string>({
    mutationFn: async (url: string) => {
      const { data } = await api.post("/skills/install/url", { url });
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["skills"] }),
  });
}

export function useUninstallSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.delete(`/skills/${name}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["skills"] }),
  });
}

// ── 版本历史 ──────────────────────────────────────────────

export function useSkillVersions(name: string, enabled = true) {
  return useQuery<VersionInfo[]>({
    queryKey: ["skill-versions", name],
    queryFn: async () => (await api.get(`/skills/${name}/versions`)).data,
    enabled: !!name && enabled,
    staleTime: 10_000,
  });
}

export function useRollbackSkill() {
  const qc = useQueryClient();
  return useMutation<InstallResult, Error, { name: string; index: number }>({
    mutationFn: async ({ name, index }) => {
      const { data } = await api.post(`/skills/${name}/rollback`, { index });
      return data;
    },
    onSuccess: (_data, { name }) => {
      qc.invalidateQueries({ queryKey: ["skills"] });
      qc.invalidateQueries({ queryKey: ["skill-versions", name] });
    },
  });
}

// ── SSE 热重载事件 ─────────────────────────────────────────

/**
 * 订阅技能热重载 SSE 事件流。
 * 每次收到 skill_reloaded 事件时自动刷新技能列表，并调用 onReload 回调。
 */
export function useSkillEvents(
  onReload?: (event: SkillReloadedEvent) => void
) {
  const qc = useQueryClient();
  const esRef = useRef<EventSource | null>(null);
  // 用 ref 避免回调过期
  const onReloadRef = useRef(onReload);
  useEffect(() => { onReloadRef.current = onReload; }, [onReload]);

  useEffect(() => {
    // 相对路径，通过 Vite proxy 转发（vite.config.ts 已设置 x-accel-buffering: no）
    const url = `/api/v1/skills/events`;

    let es: EventSource;
    let retryTimeout: ReturnType<typeof setTimeout>;
    let active = true;

    const connect = () => {
      if (!active) return;
      es = new EventSource(url);
      esRef.current = es;

      es.onmessage = (e) => {
        try {
          const event = JSON.parse(e.data) as { type: string };
          if (event.type === "skill_reloaded") {
            qc.invalidateQueries({ queryKey: ["skills"] });
            onReloadRef.current?.(event as SkillReloadedEvent);
          }
          // type==="ping" 忽略即可
        } catch {
          // ignore parse errors
        }
      };

      es.onerror = () => {
        es.close();
        if (active) {
          // 3 秒后重连
          retryTimeout = setTimeout(connect, 3000);
        }
      };
    };

    connect();

    return () => {
      active = false;
      clearTimeout(retryTimeout);
      es?.close();
      esRef.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
}
