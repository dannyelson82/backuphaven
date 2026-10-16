/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// The backend (uvicorn) listens on 7272 in development; the dev server
// forwards API calls to it so the browser only talks to one origin.
const backend = process.env.BACKUPHAVEN_BACKEND ?? 'http://127.0.0.1:7272';

export default defineConfig({
  plugins: [react()],
  // Relative asset paths so the built UI works behind any path prefix
  // (reverse proxies, code-server's port proxy).
  base: './',
  // One bundle is fine for a LAN app; split later if it grows much further.
  build: { chunkSizeWarningLimit: 1500 },
  server: {
    port: 5173,
    // The user guide (docs/guide) lives outside the frontend folder; allow just that.
    fs: { allow: ['.', '../docs/guide'] },
    proxy: {
      '/api': { target: backend, ws: true },
      '/healthz': backend,
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
