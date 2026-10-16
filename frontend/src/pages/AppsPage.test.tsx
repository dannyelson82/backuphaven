import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { EMPTY_STATUS, mockApi, state } from '../test/mockApi';

const LOGGED_IN = state({ authenticated: true, username: 'admin', method: 'session' });

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = '';
});

const app = (name: string, extra: Record<string, unknown> = {}) => ({
  name,
  image: `lscr.io/linuxserver/${name}`,
  plugin: name,
  folders: [{ target: '/config', host: `/mnt/user/appdata/${name}`, share: 'appdata' }],
  missing: [],
  scheduled: false,
  not_installed: null,
  last_backup: null,
  ...extra,
});

it('adds apps to the backup schedule and removes them', async () => {
  let scheduled: string[] = ['gone-app'];
  const calls = mockApi({
    'GET auth/state': LOGGED_IN,
    'GET status': { body: EMPTY_STATUS },
    'GET apps/discovered': () => ({
      body: {
        data_shares: ['appdata'],
        templates_found: true,
        scheduled,
        apps: [
          app('plex', {
            scheduled: scheduled.includes('plex'),
            last_backup: {
              run_id: 3,
              started_at: new Date().toISOString(),
              status: 'ok',
              bytes: 172_000_000,
            },
          }),
          app('sonarr', { scheduled: scheduled.includes('sonarr') }),
          app('oldthing', {
            not_installed: "not installed: /mnt/user/appdata/oldthing doesn't exist",
          }),
        ],
      },
    }),
    'PUT schedule/apps': (init) => {
      scheduled = JSON.parse(String(init?.body)).apps;
      return { body: { apps: scheduled } };
    },
  });
  window.location.hash = '#/apps';
  render(<App />);
  // A scheduled app whose template is gone can be removed.
  expect(await screen.findByText('gone-app is no longer found')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('radio', { name: /All apps/ }));
  expect(screen.getByText('Not installed')).toBeInTheDocument();
  expect(screen.getByText(/172 MB/)).toBeInTheDocument();
  const buttons = screen.getAllByRole('button', { name: 'Add to backup schedule' });
  await userEvent.click(buttons[0]); // plex
  expect(await screen.findByText('In the backup schedule')).toBeInTheDocument();
  const put = calls.filter((c) => c.key === 'PUT schedule/apps').at(-1);
  expect(JSON.parse(String(put?.init?.body))).toEqual({ apps: ['gone-app', 'plex'] });

  const alert = screen.getByText('gone-app is no longer found').closest('[role="alert"]');
  await userEvent.click(within(alert as HTMLElement).getByRole('button', { name: 'Remove' }));
  const last = calls.filter((c) => c.key === 'PUT schedule/apps').at(-1);
  expect(JSON.parse(String(last?.init?.body))).toEqual({ apps: ['plex'] });

  await userEvent.type(screen.getByRole('textbox', { name: 'Search apps' }), 'son');
  expect(screen.queryByText('plex')).not.toBeInTheDocument();
  expect(screen.getByText('sonarr')).toBeInTheDocument();
});

it('offers the backups that hold an app when restoring it', async () => {
  mockApi({
    'GET auth/state': LOGGED_IN,
    'GET status': { body: EMPTY_STATUS },
    'GET apps/discovered': {
      body: {
        data_shares: ['appdata'],
        templates_found: true,
        scheduled: ['sonarr'],
        apps: [
          app('sonarr', {
            scheduled: true,
            last_backup: {
              run_id: 5,
              started_at: new Date().toISOString(),
              status: 'ok',
              bytes: 1000,
            },
          }),
        ],
      },
    },
    'GET backups': {
      body: [
        { run_id: 5, started_at: '2026-10-17T03:00:00+00:00', apps: ['sonarr', 'plex'] },
        { run_id: 4, started_at: '2026-10-16T03:00:00+00:00', apps: ['plex'] },
      ],
    },
    'GET backups/5/contents': {
      body: { run_id: 5, archive: 'b.tar.zst', created: null, only: null, apps: [] },
    },
  });
  window.location.hash = '#/apps';
  render(<App />);
  await userEvent.click(await screen.findByRole('button', { name: 'Restore…' }));
  const dialog = await screen.findByRole('dialog');
  const choices = within(dialog)
    .getAllByRole('button')
    .filter((b) => b.textContent?.includes('Oct'));
  expect(choices).toHaveLength(1);
  await userEvent.click(choices[0]);
  expect(await screen.findByRole('heading', { name: 'Restore sonarr' })).toBeInTheDocument();
});
