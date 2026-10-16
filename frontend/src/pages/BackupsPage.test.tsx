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

it('opens a backup to show its apps and files with download links', async () => {
  mockApi({
    'GET auth/state': LOGGED_IN,
    'GET status': { body: EMPTY_STATUS },
    'GET backups': { body: [backup(1)] },
    'GET backups/1/contents': {
      body: {
        run_id: 1,
        archive: 'backuphaven-2026-10-17T030000.tar.zst',
        created: '2026-10-17T03:00:00+00:00',
        only: null,
        apps: [
          {
            key: 'sonarr',
            name: 'sonarr',
            plugin: 'sonarr',
            status: 'ok',
            files: 3,
            bytes: 249_000_000,
            databases: [{ path: 'sonarr.db' }],
            warnings: [],
            errors: [],
          },
          {
            key: '',
            name: 'oldapp',
            plugin: 'generic',
            status: 'skipped',
            files: 0,
            bytes: 0,
            databases: [],
            warnings: ['not installed'],
            errors: [],
          },
        ],
      },
    },
    'GET backups/1/apps/sonarr/entries': {
      body: [
        { path: 'files/config/config.xml', kind: 'file', size: 120 },
        { path: 'databases/config/sonarr.db', kind: 'file', size: 249_000_000 },
        { path: 'files/config/latest', kind: 'symlink', size: 0 },
      ],
    },
  });
  render(<App />);
  await userEvent.click(await screen.findByRole('link', { name: 'Backups' }));
  await userEvent.click(await screen.findByRole('link', { name: 'Open' }));
  expect(await screen.findByRole('link', { name: 'Download backup' })).toHaveAttribute(
    'href',
    'api/v1/backups/1/download',
  );
  await userEvent.click(screen.getByRole('button', { name: /sonarr/ }));
  expect(await screen.findByRole('link', { name: 'files/config/config.xml' })).toHaveAttribute(
    'href',
    'api/v1/backups/1/apps/sonarr/file?path=files%2Fconfig%2Fconfig.xml',
  );
  expect(screen.getByRole('link', { name: 'Download sonarr' })).toHaveAttribute(
    'href',
    'api/v1/backups/1/apps/sonarr/download',
  );
  expect(screen.getByText('files/config/latest (link)')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: /oldapp/ })).toBeDisabled();
});
