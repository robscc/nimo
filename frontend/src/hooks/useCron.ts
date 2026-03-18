import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listCronJobs,
  createCronJob,
  updateCronJob,
  deleteCronJob,
  toggleCronJob,
  listCronExecutions,
  getCronExecutionDetail,
  type CronJobInfo,
  type CronJobCreate,
  type CronJobUpdate,
  type CronExecutionInfo,
  type CronExecutionDetail,
} from "../api";

export function useCronJobs() {
  return useQuery<CronJobInfo[]>({
    queryKey: ["cron-jobs"],
    queryFn: listCronJobs,
    staleTime: 10000,
  });
}

export function useCreateCronJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: CronJobCreate) => createCronJob(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cron-jobs"] }),
  });
}

export function useUpdateCronJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: CronJobUpdate }) =>
      updateCronJob(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cron-jobs"] }),
  });
}

export function useDeleteCronJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteCronJob(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cron-jobs"] }),
  });
}

export function useToggleCronJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      toggleCronJob(id, enabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["cron-jobs"] }),
  });
}

export function useCronExecutions(jobId: string | undefined) {
  return useQuery<CronExecutionInfo[]>({
    queryKey: ["cron-executions", jobId],
    queryFn: () => listCronExecutions(jobId!, 20),
    enabled: !!jobId,
    staleTime: 30_000,
  });
}

export function useCronExecutionDetail(executionId: string | undefined) {
  return useQuery<CronExecutionDetail>({
    queryKey: ["cron-execution-detail", executionId],
    queryFn: () => getCronExecutionDetail(executionId!),
    enabled: !!executionId,
  });
}
