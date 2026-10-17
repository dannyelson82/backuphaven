import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './client';

export type ChannelType = 'ntfy' | 'discord' | 'homeassistant' | 'email' | 'unraid';

export interface ChannelView {
  id: string;
  type: ChannelType;
  name: string;
  enabled: boolean;
  kinds: string[];
  settings: Record<string, string | number>;
  has_secret: boolean;
}

export interface AlertSettings {
  channels: ChannelView[];
  summary_enabled: boolean;
  summary_time: string;
  kinds: Record<string, string>;
}

export interface ChannelInput {
  type: ChannelType;
  name: string;
  enabled: boolean;
  kinds: string[];
  settings: Record<string, string | number>;
  secret: string | null;
}

const KEY = ['settings', 'alerts'] as const;

export function useAlertSettings() {
  return useQuery({ queryKey: KEY, queryFn: () => api<AlertSettings>('settings/alerts') });
}

export function useSaveChannel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string | null; body: ChannelInput }) =>
      api<AlertSettings>(id ? `settings/alerts/channels/${id}` : 'settings/alerts/channels', {
        method: id ? 'PUT' : 'POST',
        body: JSON.stringify(body),
      }),
    onSuccess: (data) => queryClient.setQueryData(KEY, data),
  });
}

export function useRemoveChannel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<AlertSettings>(`settings/alerts/channels/${id}`, { method: 'DELETE' }),
    onSuccess: (data) => queryClient.setQueryData(KEY, data),
  });
}

export function useTestChannel() {
  return useMutation({
    mutationFn: (id: string) =>
      api<{ ok: boolean; error: string | null }>(`settings/alerts/channels/${id}/test`, {
        method: 'POST',
      }),
  });
}

export function useSaveSummary() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { summary_enabled: boolean; summary_time: string }) =>
      api<AlertSettings>('settings/alerts/summary', { method: 'PUT', body: JSON.stringify(body) }),
    onSuccess: (data) => queryClient.setQueryData(KEY, data),
  });
}
