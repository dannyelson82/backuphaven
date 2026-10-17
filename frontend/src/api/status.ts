import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';

export type RunStatus = 'running' | 'ok' | 'warning' | 'failed' | 'cancelled' | 'skipped';

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
    skipped?: number;
    files: number;
    seconds: number;
    // Set when Run now was limited to some apps.
    only?: string[];
    chosen?: boolean;
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
  seconds?: number;
  timing?: Record<string, unknown>;
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
  scheduled_apps: string[];
  open_alerts?: { id: number; kind: string; title: string; message: string; since: string }[];
  next_run: string | null;
  missed: { due: string; reason: string }[];
  last_run: Run | null;
  last_good_run: Run | null;
  apps: number;
  data_shares: string[];
  templates_found: boolean;
  missing_shares: { app: string; target: string; share: string }[];
  backups_free_bytes: number | null;
  backups_count: number;
  backups_bytes: number;
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

/** Start a backup; with `apps`, only those apps are backed up. */
export function useRunNow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (apps?: string[]) =>
      api<{ run_id: number }>('runs', {
        method: 'POST',
        ...(apps ? { body: JSON.stringify({ apps }) } : {}),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: STATUS_KEY }),
  });
}

export function useCancelRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api<{ cancelling: boolean }>('runs/cancel', { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: STATUS_KEY }),
  });
}

export interface DiscoveredApp {
  name: string;
  image: string;
  // null: no plugin yet, so the app can't be backed up.
  plugin: string | null;
  plugin_name?: string | null;
  plugin_local?: boolean;
  external_databases?: string[];
  folders: { target: string; host: string; share: string }[];
  missing: { target: string; host: string; share: string }[];
  scheduled: boolean;
  not_installed: string | null;
  last_backup: { run_id: number; started_at: string; status: RunStatus; bytes: number } | null;
}

export function useDiscoveredApps(enabled: boolean) {
  return useQuery({
    queryKey: ['apps', 'discovered'],
    queryFn: () =>
      api<{ apps: DiscoveredApp[]; scheduled: string[]; data_shares: string[] }>('apps/discovered'),
    enabled,
  });
}

export function useScheduleApps() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (apps: string[]) =>
      api<{ apps: string[] }>('schedule/apps', { method: 'PUT', body: JSON.stringify({ apps }) }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['apps', 'discovered'] });
      void queryClient.invalidateQueries({ queryKey: STATUS_KEY });
    },
  });
}
