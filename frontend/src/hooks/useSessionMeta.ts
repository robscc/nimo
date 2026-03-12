import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getSessionMeta, updateSessionConfig } from "../api";
import type { SessionMeta, SessionConfigUpdate } from "../api";

export function useSessionMeta(sessionId: string | null) {
  return useQuery<SessionMeta | null>({
    queryKey: ["session-meta", sessionId],
    queryFn: () => (sessionId ? getSessionMeta(sessionId) : null),
    enabled: !!sessionId,
    staleTime: 5000,
  });
}

export function useUpdateSessionConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      sessionId,
      config,
    }: {
      sessionId: string;
      config: SessionConfigUpdate;
    }) => updateSessionConfig(sessionId, config),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["session-meta", vars.sessionId] });
    },
  });
}
