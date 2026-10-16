import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';
import type { RunStatus } from './status';

export interface Backup {
  run_id: number;
  archive: string;
  started_at: string;
  trigger: string;
  status: RunStatus;
  size: number | null;
  verified: boolean;
  summary: { apps?: number; only?: string[]; chosen?: boolean } | null;
  databases: number;
  healthy: number;
  damaged: number;
  keep_reasons: string[];
  protected: boolean;
  on_disk: boolean;
}

const KEY = ['backups'] as const;

export function useBackups() {
  return useQuery({ queryKey: KEY, queryFn: () => api<Backup[]>('backups') });
}

export function useDeleteBackup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (runId: number) => api<void>(`backups/${runId}`, { method: 'DELETE' }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: KEY }),
  });
}
