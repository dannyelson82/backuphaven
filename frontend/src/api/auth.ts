import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, json, rememberCsrfToken } from './client';

export interface AuthState {
  setup_required: boolean;
  authenticated: boolean;
  username: string | null;
  method: 'session' | 'bypass' | 'api_key' | null;
  csrf_token: string;
}

export interface SecurityView {
  local_bypass: boolean;
  trusted_proxies: string[];
  session_idle_days: number;
  api_key: { prefix: string; created_at: string } | null;
  client_ip: string | null;
  bypass_applies_to_you: boolean;
  gateway_warning: boolean;
}

export type SecurityUpdate = Pick<
  SecurityView,
  'local_bypass' | 'trusted_proxies' | 'session_idle_days'
>;

interface Credentials {
  username: string;
  password: string;
  remember?: boolean;
}

const AUTH_KEY = ['auth-state'] as const;
const SECURITY_KEY = ['security'] as const;

async function fetchAuthState(): Promise<AuthState> {
  const state = await api<AuthState>('auth/state');
  rememberCsrfToken(state.csrf_token);
  return state;
}

export function useAuthState() {
  return useQuery({ queryKey: AUTH_KEY, queryFn: fetchAuthState });
}

function useAuthMutation<TBody>(request: (body: TBody) => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: request,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: AUTH_KEY }),
  });
}

export const useSetup = () =>
  useAuthMutation((body: Credentials) => api('setup', { method: 'POST', body: json(body) }));

export const useLogin = () =>
  useAuthMutation((body: Credentials) => api('auth/login', { method: 'POST', body: json(body) }));

export const useLogout = () => useAuthMutation(() => api('auth/logout', { method: 'POST' }));

export const useLogoutEverywhere = () =>
  useAuthMutation(() => api('auth/logout-all', { method: 'POST' }));

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      api('auth/password', { method: 'POST', body: json(body) }),
  });
}

export function useSecurity() {
  return useQuery({
    queryKey: SECURITY_KEY,
    queryFn: () => api<SecurityView>('settings/security'),
  });
}

export function useUpdateSecurity() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SecurityUpdate) =>
      api<SecurityView>('settings/security', { method: 'PUT', body: json(body) }),
    onSuccess: (view) => queryClient.setQueryData(SECURITY_KEY, view),
  });
}

export function useRotateApiKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api<{ api_key: string; prefix: string }>('settings/security/api-key', { method: 'POST' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: SECURITY_KEY }),
  });
}
