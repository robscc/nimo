import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createSession, getSessions } from "../api";

export function useSessions(channel = "web") {
  return useQuery({
    queryKey: ["sessions", channel],
    queryFn: () => getSessions(channel),
    staleTime: 10_000,
  });
}

export function useCreateSession() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (channel?: string) => createSession(channel ?? "web"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sessions"] }),
  });
}
