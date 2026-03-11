import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "../api";

export interface ToolInfo {
  name: string;
  description: string;
  icon: string;
  dangerous: boolean;
  enabled: boolean;
}

export interface ToolLog {
  id: string;
  session_id: string;
  tool_name: string;
  input: Record<string, unknown>;
  output: string | null;
  error: string | null;
  duration_ms: number | null;
  created_at: string;
}

export function useTools() {
  return useQuery<ToolInfo[]>({
    queryKey: ["tools"],
    queryFn: async () => (await api.get("/tools")).data,
    staleTime: 5000,
  });
}

export function useToggleTool() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, enabled }: { name: string; enabled: boolean }) =>
      api.patch(`/tools/${name}`, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["tools"] }),
  });
}

export function useToolLogs(toolName?: string) {
  return useQuery<ToolLog[]>({
    queryKey: ["tool-logs", toolName],
    queryFn: async () => {
      const params = toolName ? { tool_name: toolName, limit: 50 } : { limit: 50 };
      return (await api.get("/tools/logs", { params })).data;
    },
    refetchInterval: 5000,
  });
}
