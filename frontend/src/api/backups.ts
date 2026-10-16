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
  apps: string[];
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

export interface ContentApp {
  key: string;
  name: string;
  plugin: string;
  status: RunStatus;
  files: number;
  bytes: number;
  databases: { path: string; status?: string; size?: number }[];
  warnings: string[];
  errors: string[];
}

export interface Contents {
  run_id: number;
  archive: string;
  created: string | null;
  only: string[] | null;
  apps: ContentApp[];
}

export interface Entry {
  path: string;
  kind: 'file' | 'symlink';
  size: number;
}

export function useContents(runId: number) {
  return useQuery({
    queryKey: ['backups', runId, 'contents'],
    queryFn: () => api<Contents>(`backups/${runId}/contents`),
  });
}

export function useEntries(runId: number, key: string | null) {
  return useQuery({
    queryKey: ['backups', runId, 'entries', key],
    queryFn: () => api<Entry[]>(`backups/${runId}/apps/${encodeURIComponent(key ?? '')}/entries`),
    enabled: key !== null,
  });
}

/** Plain links: the browser downloads with the session cookie. */
export const downloadUrl = {
  backup: (runId: number) => `api/v1/backups/${runId}/download`,
  app: (runId: number, key: string) =>
    `api/v1/backups/${runId}/apps/${encodeURIComponent(key)}/download`,
  file: (runId: number, key: string, path: string) =>
    `api/v1/backups/${runId}/apps/${encodeURIComponent(key)}/file?path=${encodeURIComponent(path)}`,
};
