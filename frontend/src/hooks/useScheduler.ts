import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getSchedulerAgents,
  getSchedulerStats,
  stopSchedulerAgent,
} from "../api";

export function useSchedulerAgents() {
  return useQuery({
    queryKey: ["scheduler-agents"],
    queryFn: getSchedulerAgents,
    refetchInterval: 5000,
  });
}

export function useSchedulerStats() {
  return useQuery({
    queryKey: ["scheduler-stats"],
    queryFn: getSchedulerStats,
    refetchInterval: 10000,
  });
}

export function useStopAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: stopSchedulerAgent,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["scheduler-agents"] });
      qc.invalidateQueries({ queryKey: ["scheduler-stats"] });
    },
  });
}
