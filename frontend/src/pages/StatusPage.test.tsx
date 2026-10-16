import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { EMPTY_STATUS, mockApi, state } from '../test/mockApi';

const LOGGED_IN = state({ authenticated: true, username: 'admin', method: 'session' });

const RUN = {
  id: 7,
  trigger: 'manual',
  started_at: new Date(Date.now() - 5 * 60_000).toISOString(),
  finished_at: new Date().toISOString(),
  status: 'warning',
  archive: 'backuphaven-2026-10-17T030000.tar.zst',
  size: 4_200_000_000,
  verified: true,
  error: null,
  summary: { apps: 3, failed: 0, warnings: 1, files: 120, seconds: 75 },
};

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = '';
});

it('shows the last backup, database health and app results', async () => {
  mockApi({
    'GET auth/state': LOGGED_IN,
    'GET status': {
      body: {
        ...EMPTY_STATUS,
        apps: 3,
        data_shares: ['appdata', 'fast'],
        last_run: RUN,
        last_good_run: RUN,
        missing_shares: [{ app: 'elsewhere', target: '/config', share: 'other' }],
        database_health: [
          {
            app: 'plex',
            path: 'Library/Databases/com.plexapp.plugins.library.db',
            status: 'ok',
            mode: 'quick',
            size: 900_000_000,
            messages: [],
            unknown_collations: ['naturalsort'],
            skipped_tables: [],
          },
          {
            app: 'Tool',
            path: 'state.sqlite',
            status: 'damaged',
            mode: 'full',
            size: 1000,
            messages: ['row 3 missing from index'],
            unknown_collations: [],
            skipped_tables: [],
          },
        ],
      },
    },
    'GET runs/7': {
      body: {
        ...RUN,
        apps: [
          {
            app: 'sonarr',
            plugin: 'sonarr',
            status: 'warning',
            files: 12,
            bytes: 30_000_000,
            seconds: 2,
            errors: [],
            warnings: ['config.xml changed while it was read'],
          },
          {
            app: 'oldapp',
            plugin: 'generic',
            status: 'skipped',
            files: 0,
            bytes: 0,
            seconds: 0,
            errors: [],
            warnings: [
              "not installed: /mnt/user/appdata/oldapp doesn't exist (container removed?)",
            ],
          },
        ],
        databases: [],
      },
    },
  });
  render(<App />);
  expect(await screen.findByText('Warnings')).toBeInTheDocument();
  expect(screen.getByText('5 min ago')).toBeInTheDocument();
  expect(screen.getByText(/4\.2 GB · 3 apps · 1:15/)).toBeInTheDocument();
  expect(screen.getByText(/checked after writing/)).toBeInTheDocument();
  expect(screen.getByText(/Some app data is on a share/)).toBeInTheDocument();
  expect(screen.getByText('/mnt/user/other')).toBeInTheDocument();
  expect(screen.getByText('com.plexapp.plugins.library.db')).toBeInTheDocument();
  expect(screen.getByText('quick check')).toBeInTheDocument();
  expect(screen.getByText('Damaged')).toBeInTheDocument();
  expect(screen.getByText('row 3 missing from index')).toBeInTheDocument();
  expect(await screen.findByText('config.xml changed while it was read')).toBeInTheDocument();
  expect(screen.getByText('Skipped (1)')).toBeInTheDocument();
  expect(screen.getByText(/oldapp: not installed/)).toBeInTheDocument();
});

it('starts a backup and shows its progress', async () => {
  let started = false;
  const calls = mockApi({
    'GET auth/state': LOGGED_IN,
    'GET status': () => ({
      body: started
        ? {
            ...EMPTY_STATUS,
            running: {
              run_id: 1,
              seconds: 12,
              app: 'plex',
              apps_done: 1,
              apps_total: 3,
              files: 40,
              bytes: 5_000_000,
              phase: 'backing up',
            },
          }
        : { ...EMPTY_STATUS, scheduled_apps: ['plex', 'sonarr'] },
    }),
    'POST runs': () => {
      started = true;
      return { status: 202, body: { run_id: 1 } };
    },
  });
  render(<App />);
  expect(
    await screen.findByText(/backs up the 2 apps in the backup schedule: plex, sonarr/),
  ).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Run now' }));
  expect(await screen.findByText('Backing up plex')).toBeInTheDocument();
  expect(screen.getByText(/1 of 3 apps · 40 files · 5\.0 MB/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Run now' })).toBeDisabled();
  expect(calls.some((c) => c.key === 'POST runs')).toBe(true);
});

const RUNNING = {
  run_id: 2,
  seconds: 30,
  app: 'plex',
  apps_done: 0,
  apps_total: 2,
  files: 10,
  bytes: 1_000_000,
  phase: 'backing up',
};

it('backs up only the apps chosen', async () => {
  let started = false;
  const calls = mockApi({
    'GET auth/state': LOGGED_IN,
    'GET status': () => ({
      body: started ? { ...EMPTY_STATUS, running: RUNNING } : { ...EMPTY_STATUS, apps: 3 },
    }),
    'GET apps/discovered': {
      body: {
        data_shares: ['appdata'],
        templates_found: true,
        apps: [
          {
            name: 'plex',
            image: 'p',
            plugin: 'plex',
            folders: [{ target: '/config', host: '/mnt/user/appdata/plex', share: 'appdata' }],
            missing: [],
          },
          {
            name: 'sonarr',
            image: 's',
            plugin: 'sonarr',
            folders: [{ target: '/config', host: '/mnt/user/appdata/sonarr', share: 'appdata' }],
            missing: [],
          },
          {
            name: 'radarr',
            image: 'r',
            plugin: 'radarr',
            folders: [],
            missing: [{ target: '/config', host: '/mnt/user/other/radarr', share: 'other' }],
          },
        ],
      },
    },
    'POST runs': () => {
      started = true;
      return { status: 202, body: { run_id: 2 } };
    },
  });
  render(<App />);
  await userEvent.click(await screen.findByRole('button', { name: 'Choose apps…' }));
  expect(await screen.findByText(/on the other share/)).toBeInTheDocument();
  const start = screen.getByRole('button', { name: 'Back up 0 apps' });
  expect(start).toBeDisabled();
  await userEvent.click(screen.getByRole('checkbox', { name: 'plex' }));
  await userEvent.click(screen.getByRole('checkbox', { name: 'sonarr' }));
  await userEvent.click(screen.getByRole('button', { name: 'Back up 2 apps' }));
  expect(await screen.findByText('Backing up plex')).toBeInTheDocument();
  const post = calls.find((c) => c.key === 'POST runs');
  expect(JSON.parse(String(post?.init?.body))).toEqual({ apps: ['plex', 'sonarr'] });
});

it('cancels a running backup', async () => {
  let cancelled = false;
  const calls = mockApi({
    'GET auth/state': LOGGED_IN,
    'GET status': () => ({
      body: { ...EMPTY_STATUS, running: cancelled ? { ...RUNNING, phase: 'cancelling' } : RUNNING },
    }),
    'POST runs/cancel': () => {
      cancelled = true;
      return { status: 202, body: { cancelling: true } };
    },
  });
  render(<App />);
  await userEvent.click(await screen.findByRole('button', { name: 'Cancel' }));
  expect(await screen.findByText('Cancelling…')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
  expect(calls.some((c) => c.key === 'POST runs/cancel')).toBe(true);
});

it('shows a cancelled backup and a backup of chosen apps', async () => {
  mockApi({
    'GET auth/state': LOGGED_IN,
    'GET status': {
      body: {
        ...EMPTY_STATUS,
        last_run: {
          ...RUN,
          status: 'cancelled',
          archive: null,
          size: null,
          verified: false,
          summary: { ...RUN.summary, only: ['plex'] },
        },
      },
    },
    'GET runs/7': { body: { ...RUN, apps: [], databases: [] } },
  });
  render(<App />);
  expect(await screen.findByText('Cancelled')).toBeInTheDocument();
  expect(screen.getByText('Chosen apps only: plex')).toBeInTheDocument();
});

it('says when the container list is missing and when a backup failed', async () => {
  mockApi({
    'GET auth/state': LOGGED_IN,
    'GET status': {
      body: {
        ...EMPTY_STATUS,
        templates_found: false,
        last_run: { ...RUN, status: 'failed', error: 'not enough free space on the backups disk' },
      },
    },
    'GET runs/7': { body: { ...RUN, apps: [], databases: [] } },
  });
  render(<App />);
  expect(await screen.findByText("BackupHaven can't see your containers")).toBeInTheDocument();
  expect(screen.getByText('The last backup failed')).toBeInTheDocument();
  expect(screen.getByText('not enough free space on the backups disk')).toBeInTheDocument();
});

it('asks for apps in the backup schedule before Run now works', async () => {
  mockApi({ 'GET auth/state': LOGGED_IN, 'GET status': { body: { ...EMPTY_STATUS, apps: 5 } } });
  render(<App />);
  expect(await screen.findByText('Nothing is in the backup schedule yet')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Run now' })).toBeDisabled();
  expect(screen.getByText('0 of 5 apps')).toBeInTheDocument();
});
