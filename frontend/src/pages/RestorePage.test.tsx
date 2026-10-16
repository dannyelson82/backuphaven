import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { EMPTY_STATUS, mockApi, state } from '../test/mockApi';

const LOGGED_IN = state({ authenticated: true, username: 'admin', method: 'session' });

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = '';
});

it('walks through stopping the container, restoring and undoing', async () => {
  let checks = 0;
  let restoreStatus = 'running';
  const calls = mockApi({
    'GET auth/state': LOGGED_IN,
    'GET status': { body: EMPTY_STATUS },
    'GET backups/4/contents': {
      body: {
        run_id: 4,
        archive: 'backuphaven-2026-10-17T030000.tar.zst',
        created: '2026-10-17T03:00:00+00:00',
        only: null,
        apps: [],
      },
    },
    'POST restores/check': () => {
      checks += 1;
      return {
        body: {
          app: 'sonarr',
          folders: [
            { target: '/config', host: '/mnt/user/appdata/sonarr', exists: true, in_backup: true },
          ],
          changing: checks === 1 ? ['/mnt/user/appdata/sonarr/logs.db'] : [],
          stopped: checks > 1,
        },
      };
    },
    'POST restores': { status: 202, body: { restore_id: 9 } },
    'GET restores/current': () => ({
      body:
        restoreStatus === 'running'
          ? { restore_id: 9, app: 'sonarr', phase: 'unpacking the backup', seconds: 2 }
          : null,
    }),
    'GET restores/9': () => {
      const body = {
        id: 9,
        run_id: 4,
        app: 'sonarr',
        status: restoreStatus,
        started_at: new Date().toISOString(),
        finished_at: null,
        undo_until: '2026-10-24T03:00:00+00:00',
        error: null,
        detail: {
          folders: [{ host: '/mnt/user/appdata/sonarr', aside: '/x', moved_back: ['logs'] }],
        },
        actor: 'admin',
      };
      restoreStatus = 'done';
      return { body };
    },
    'POST restores/9/undo': { body: { id: 9, status: 'undone' } },
  });
  window.location.hash = '#/restore/4/sonarr';
  render(<App />);
  expect(await screen.findByText(/kept aside for 7 days/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: "Check it's stopped" }));
  expect(await screen.findByText('sonarr still seems to be running')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: "Check it's stopped" }));
  const confirm = await screen.findByRole('checkbox', { name: /sonarr is stopped/ });
  expect(screen.getByRole('button', { name: 'Restore sonarr' })).toBeDisabled();
  await userEvent.click(confirm);
  await userEvent.click(screen.getByRole('button', { name: 'Restore sonarr' }));
  const post = calls.find((c) => c.key === 'POST restores');
  expect(JSON.parse(String(post?.init?.body))).toEqual({
    run_id: 4,
    app: 'sonarr',
    confirmed_stopped: true,
  });
  expect(await screen.findByText('sonarr is restored', {}, { timeout: 4000 })).toBeInTheDocument();
  expect(screen.getByText(/caches and posters/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
  expect(await screen.findByText('Undone')).toBeInTheDocument();
});
