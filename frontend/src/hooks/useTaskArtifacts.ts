import { useQuery } from "@tanstack/react-query";
import { getTaskArtifacts, type TaskArtifact } from "../api";

export function useTaskArtifacts(taskId: string | null) {
  return useQuery<TaskArtifact[]>({
    queryKey: ["task-artifacts", taskId],
    queryFn: () => getTaskArtifacts(taskId!),
    enabled: !!taskId,
    staleTime: 30_000,
  });
}
