import { vi } from 'vitest';

export interface MockResponse {
  status?: number;
  body?: unknown;
  headers?: Record<string, string>;
}

type Handler = (init: RequestInit | undefined) => MockResponse;

/** Replace fetch with a router keyed by "METHOD path" (path relative to api/v1). */
export function mockApi(routes: Record<string, MockResponse | Handler>) {
  const calls: { key: string; init: RequestInit | undefined }[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input).replace(/^api\/v1\//, '');
    const key = `${(init?.method ?? 'GET').toUpperCase()} ${path}`;
    calls.push({ key, init });
    const route = routes[key];
    if (!route) return new Response(JSON.stringify({ detail: 'not_mocked' }), { status: 404 });
    const res = typeof route === 'function' ? route(init) : route;
    const status = res.status ?? 200;
    const payload = 'body' in res ? res.body : {};
    return new Response(status === 204 ? null : JSON.stringify(payload), {
      status,
      headers: res.headers,
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  return calls;
}

export const state = (overrides: Record<string, unknown> = {}) => ({
  body: {
    setup_required: false,
    authenticated: false,
    username: null,
    method: null,
    csrf_token: 'csrf-123',
    ...overrides,
  },
});
