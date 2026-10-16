import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';

export interface ScheduleSettings {
  enabled: boolean;
  time: string; // "HH:MM", server time zone
  days: number[]; // 0 = Monday
}

export interface ScheduleView extends ScheduleSettings {
  timezone: string;
  next_run: string | null;
  apps: string[];
  missed: { due: string; reason: string }[];
}

const KEY = ['settings', 'schedule'] as const;

export function useSchedule() {
  return useQuery({ queryKey: KEY, queryFn: () => api<ScheduleView>('settings/schedule') });
}

export function useSaveSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (settings: ScheduleSettings) =>
      api<ScheduleView>('settings/schedule', { method: 'PUT', body: JSON.stringify(settings) }),
    onSuccess: (data) => {
      queryClient.setQueryData(KEY, data);
      void queryClient.invalidateQueries({ queryKey: ['status'] });
    },
  });
}
