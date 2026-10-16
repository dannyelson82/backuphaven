import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';

export interface DeveloperSettings {
  diagnostics: boolean;
  folder: string;
  files: { name: string; size: number; modified: string }[];
}

const KEY = ['settings', 'developer'] as const;

export function useDeveloperSettings() {
  return useQuery({ queryKey: KEY, queryFn: () => api<DeveloperSettings>('settings/developer') });
}

export function useSetDiagnostics() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (diagnostics: boolean) =>
      api<DeveloperSettings>('settings/developer', {
        method: 'PUT',
        body: JSON.stringify({ diagnostics }),
      }),
    onSuccess: (data) => queryClient.setQueryData(KEY, data),
  });
}
