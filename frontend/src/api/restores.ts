import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';

export interface RestoreCheck {
  app: string;
  folders: { target: string; host: string; exists: boolean; in_backup: boolean }[];
  changing: string[];
  stopped: boolean;
  auto_stop?: boolean;
}

export interface Restore {
  id: number;
  run_id: number;
  app: string;
  status: 'running' | 'done' | 'failed' | 'undone' | 'expired';
  started_at: string;
  finished_at: string | null;
  undo_until: string | null;
  error: string | null;
  detail: {
    archive?: string;
    folders?: { host: string; aside: string | null; moved_back: string[]; files?: number }[];
  } | null;
  actor: string;
}

export interface RestoreProgress {
  restore_id: number;
  app: string;
  phase: string;
  seconds: number;
}

const LIST = ['restores'] as const;

export function useCheckRestore() {
  return useMutation({
    mutationFn: (body: { run_id: number; app: string }) =>
      api<RestoreCheck>('restores/check', { method: 'POST', body: JSON.stringify(body) }),
  });
}

export function useStartRestore() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { run_id: number; app: string }) =>
      api<{ restore_id: number }>('restores', {
        method: 'POST',
        body: JSON.stringify({ ...body, confirmed_stopped: true }),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: LIST }),
  });
}

export function useRestore(id: number | null) {
  return useQuery({
    queryKey: ['restores', id],
    queryFn: () => api<Restore>(`restores/${id}`),
    enabled: id !== null,
    refetchInterval: (query) => (query.state.data?.status === 'running' ? 1500 : false),
  });
}

export function useRestoreProgress(enabled: boolean) {
  return useQuery({
    queryKey: ['restores', 'current'],
    queryFn: () => api<RestoreProgress | null>('restores/current'),
    enabled,
    refetchInterval: enabled ? 1500 : false,
  });
}

export function useRestores() {
  return useQuery({ queryKey: LIST, queryFn: () => api<Restore[]>('restores') });
}

export function useUndoRestore() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api<Restore>(`restores/${id}/undo`, { method: 'POST' }),
    onSettled: () => queryClient.invalidateQueries({ queryKey: LIST }),
  });
}
