import { useQuery } from "@tanstack/react-query";
import { listTasks, type TaskListParams, type TaskListResponse } from "../api";

export function useTasks(params?: TaskListParams) {
  return useQuery<TaskListResponse>({
    queryKey: ["tasks", params],
    queryFn: () => listTasks(params),
    refetchInterval: 5000,
    staleTime: 3000,
  });
}
