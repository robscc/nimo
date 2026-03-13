import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listSubAgents,
  createSubAgent,
  updateSubAgent,
  deleteSubAgent,
  type SubAgentInfo,
  type SubAgentCreate,
  type SubAgentUpdate,
} from "../api";

export function useSubAgents() {
  return useQuery<SubAgentInfo[]>({
    queryKey: ["sub-agents"],
    queryFn: listSubAgents,
    staleTime: 10000,
  });
}

export function useCreateSubAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: SubAgentCreate) => createSubAgent(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sub-agents"] }),
  });
}

export function useUpdateSubAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, data }: { name: string; data: SubAgentUpdate }) =>
      updateSubAgent(name, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sub-agents"] }),
  });
}

export function useDeleteSubAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteSubAgent(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sub-agents"] }),
  });
}
