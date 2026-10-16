import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';

export interface KeepingSettings {
  nightly: number;
  weekly: number;
  monthly: number;
  min_free_gb: number;
}

const KEY = ['settings', 'keeping'] as const;

export function useKeeping() {
  return useQuery({ queryKey: KEY, queryFn: () => api<KeepingSettings>('settings/keeping') });
}

export function useSaveKeeping() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (settings: KeepingSettings) =>
      api<KeepingSettings>('settings/keeping', { method: 'PUT', body: JSON.stringify(settings) }),
    onSuccess: (data) => queryClient.setQueryData(KEY, data),
  });
}
