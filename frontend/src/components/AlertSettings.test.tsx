import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { EMPTY_STATUS, mockApi, state } from '../test/mockApi';

const LOGGED_IN = state({ authenticated: true, username: 'admin', method: 'session' });

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = '';
});

const KINDS = {
  run_failed: 'Backup failed',
  db_damaged: 'Database damaged',
  missed: 'Backup missed',
  summary: 'Morning summary',
};

it('adds an ntfy channel and sends a test', async () => {
  let channels: unknown[] = [];
  const view = () => ({ channels, summary_enabled: false, summary_time: '08:00', kinds: KINDS });
  const calls = mockApi({
    'GET auth/state': LOGGED_IN,
    'GET status': { body: EMPTY_STATUS },
    'GET settings/alerts': () => ({ body: view() }),
    'POST settings/alerts/channels': (init) => {
      const body = JSON.parse(String(init?.body));
      channels = [{ id: 'c1', ...body, has_secret: false, secret: undefined }];
      return { status: 201, body: view() };
    },
    'POST settings/alerts/channels/c1/test': { body: { ok: true, error: null } },
  });
  window.location.hash = '#/settings';
  render(<App />);
  await userEvent.click(await screen.findByRole('button', { name: 'Add a channel' }));
  const dialog = await screen.findByRole('dialog');
  await userEvent.type(within(dialog).getByRole('textbox', { name: /^Topic/ }), 'my-backups-x7');
  await userEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
  const post = calls.find((c) => c.key === 'POST settings/alerts/channels');
  const sent = JSON.parse(String(post?.init?.body));
  expect(sent.type).toBe('ntfy');
  expect(sent.settings).toEqual({ server: 'https://ntfy.sh', topic: 'my-backups-x7' });
  expect(sent.kinds).toEqual(['run_failed', 'db_damaged', 'missed', 'summary']);
  await userEvent.click(await screen.findByRole('button', { name: 'Send test' }));
  expect(await screen.findByText(/Test sent/)).toBeInTheDocument();
});

it('shows open alerts at the top of the Dashboard', async () => {
  mockApi({
    'GET auth/state': LOGGED_IN,
    'GET status': {
      body: {
        ...EMPTY_STATUS,
        open_alerts: [
          {
            id: 1,
            kind: 'disk_low',
            title: 'Disk nearly full',
            message: 'Only 3.0 GB free',
            since: '2026-10-17T03:00:00+00:00',
          },
        ],
      },
    },
  });
  render(<App />);
  expect(await screen.findByText('Disk nearly full')).toBeInTheDocument();
  expect(screen.getByText('Only 3.0 GB free')).toBeInTheDocument();
});
