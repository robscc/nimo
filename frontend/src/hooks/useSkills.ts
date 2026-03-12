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
  created_at: string | null;
  updated_at: string | null;
}

export interface InstallResult {
  name: string;
  version: string;
  description: string;
  tools: string[];
  install_path: string;
}

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
