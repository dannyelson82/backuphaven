import { render, screen } from '@testing-library/react';
import { App } from './App';

afterEach(() => {
  vi.unstubAllGlobals();
});

it('shows the version from the server', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({ name: 'BackupHaven', version: '0.0.1' }), { status: 200 }),
      ),
    ),
  );
  render(<App />);
  expect(await screen.findByText('Version 0.0.1')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'BackupHaven' })).toBeInTheDocument();
});

it('says when the server is unreachable', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.reject(new Error('offline'))),
  );
  render(<App />);
  expect(await screen.findByText(/can't reach the BackupHaven server/i)).toBeInTheDocument();
});
