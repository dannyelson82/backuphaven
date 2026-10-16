import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';

export type RunStatus = 'running' | 'ok' | 'warning' | 'failed';

export interface Run {
  id: number;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  status: RunStatus;
  archive: string | null;
  size: number | null;
  verified: boolean;
  error: string | null;
  summary: {
    apps: number;
    failed: number;
    warnings: number;
    files: number;
    seconds: number;
  } | null;
}

export interface DbCheck {
  app: string;
  path: string;
  status: 'ok' | 'damaged' | 'unchecked' | 'failed';
  mode: 'full' | 'quick' | null;
  size: number;
  messages: string[];
  unknown_collations: string[];
  skipped_tables: string[];
}

export interface RunApp {
  app: string;
  plugin: string;
  status: RunStatus;
  files: number;
  bytes: number;
  seconds: number;
  errors: string[];
  warnings: string[];
}

export interface RunDetail extends Run {
  apps: RunApp[];
  databases: DbCheck[];
}

export interface Progress {
  run_id: number;
  seconds: number;
  app: string | null;
  apps_done: number;
  apps_total: number;
  files: number;
  bytes: number;
  phase: string;
}

export interface Status {
  running: Progress | null;
  last_run: Run | null;
  last_good_run: Run | null;
  apps: number;
  data_shares: string[];
  templates_found: boolean;
  missing_shares: { app: string; target: string; share: string }[];
  backups_free_bytes: number | null;
  database_health: DbCheck[];
}

const STATUS_KEY = ['status'] as const;

export function useStatus() {
  return useQuery({
    queryKey: STATUS_KEY,
    queryFn: () => api<Status>('status'),
    // Every 2 s while a backup runs, otherwise every 30 s.
    refetchInterval: (query) => (query.state.data?.running ? 2000 : 30000),
  });
}

export function useRun(id: number | null | undefined) {
  return useQuery({
    queryKey: ['run', id],
    queryFn: () => api<RunDetail>(`runs/${id}`),
    enabled: id !== null && id !== undefined,
  });
}

export function useRunNow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ run_id: number }>('runs', { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: STATUS_KEY }),
  });
}
