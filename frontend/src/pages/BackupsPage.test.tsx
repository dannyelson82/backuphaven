import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { EMPTY_STATUS, mockApi, state } from '../test/mockApi';

const LOGGED_IN = state({ authenticated: true, username: 'admin', method: 'session' });

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = '';
});

const backup = (runId: number, extra: Record<string, unknown> = {}) => ({
  run_id: runId,
  archive: `backuphaven-2026-10-1${runId}T030000.tar.zst`,
  started_at: new Date(Date.now() - runId * 86_400_000).toISOString(),
  trigger: 'schedule',
  status: 'ok',
  size: 170_000_000,
  verified: true,
  summary: { apps: 4 },
  databases: 8,
  healthy: 8,
  damaged: 0,
  keep_reasons: ['nightly'],
  protected: false,
  on_disk: true,
  ...extra,
});

it('lists backups, explains why they are kept and deletes one after confirming', async () => {
  let list = [
    backup(1, { keep_reasons: ['nightly', 'newest good backup'], protected: true }),
    backup(2, {
      keep_reasons: ['nightly', 'last good copy of plex/Library/x.db'],
      protected: true,
    }),
    backup(3, {
      summary: { apps: 1, only: ['sonarr'], chosen: true },
      keep_reasons: ['chosen apps'],
    }),
  ];
  const calls = mockApi({
    'GET auth/state': LOGGED_IN,
    'GET status': { body: EMPTY_STATUS },
    'GET backups': () => ({ body: list }),
    'DELETE backups/3': () => {
      list = list.filter((b) => b.run_id !== 3);
      return { status: 204 };
    },
  });
  render(<App />);
  await userEvent.click(await screen.findByRole('link', { name: 'Backups' }));
  expect(await screen.findByText(/3 backups, 510 MB in total/)).toBeInTheDocument();
  expect(screen.getByText('Kept: nightly, newest good backup')).toBeInTheDocument();
  expect(
    screen.getByText("Kept: nightly, last healthy copy of plex's database"),
  ).toBeInTheDocument();
  const deletes = screen.getAllByRole('button', { name: 'Delete' });
  expect(deletes[0]).toBeDisabled();
  expect(deletes[1]).toBeDisabled();
  await userEvent.click(deletes[2]);
  const dialog = await screen.findByRole('dialog');
  await userEvent.click(within(dialog).getByRole('button', { name: 'Delete backup' }));
  expect(calls.some((c) => c.key === 'DELETE backups/3')).toBe(true);
  expect(await screen.findByText(/2 backups/)).toBeInTheDocument();
});
