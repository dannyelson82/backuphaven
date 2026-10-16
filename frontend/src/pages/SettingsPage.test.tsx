import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { EMPTY_STATUS, mockApi, state } from '../test/mockApi';

const LOGGED_IN = state({ authenticated: true, username: 'admin', method: 'session' });

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = '';
});

it('switches the diagnostics files on from Settings → Developer', async () => {
  let on = false;
  const view = () => ({
    diagnostics: on,
    folder: '/diagnostics',
    files: on ? [{ name: 'debug.log', size: 2048, modified: new Date().toISOString() }] : [],
  });
  const calls = mockApi({
    'GET auth/state': LOGGED_IN,
    'GET status': { body: EMPTY_STATUS },
    'GET settings/developer': () => ({ body: view() }),
    'PUT settings/developer': (init) => {
      on = JSON.parse(String(init?.body)).diagnostics;
      return { body: view() };
    },
  });
  render(<App />);
  await userEvent.click(await screen.findByRole('link', { name: 'Settings' }));
  const toggle = await screen.findByRole('switch', { name: /^Diagnostics files/ });
  expect(toggle).not.toBeChecked();
  expect(screen.getByText('/diagnostics')).toBeInTheDocument();
  await userEvent.click(toggle);
  expect(await screen.findByText('debug.log')).toBeInTheDocument();
  expect(screen.getByRole('switch', { name: /^Diagnostics files/ })).toBeChecked();
  expect(calls.some((c) => c.key === 'PUT settings/developer')).toBe(true);
  await userEvent.click(screen.getByRole('link', { name: 'Dashboard' }));
  expect(await screen.findByRole('button', { name: 'Run now' })).toBeInTheDocument();
});

it('changes when the scheduled backup runs', async () => {
  let saved = { enabled: true, time: '03:00', days: [0, 1, 2, 3, 4, 5, 6] };
  const view = () => ({
    ...saved,
    timezone: 'Europe/London',
    next_run: saved.enabled ? '2026-10-17T03:00:00+01:00' : null,
    apps: ['plex'],
    missed: [],
  });
  const calls = mockApi({
    'GET auth/state': LOGGED_IN,
    'GET status': { body: EMPTY_STATUS },
    'GET settings/developer': {
      body: { diagnostics: false, folder: '/config/diagnostics', files: [] },
    },
    'GET settings/schedule': () => ({ body: view() }),
    'PUT settings/schedule': (init) => {
      saved = JSON.parse(String(init?.body));
      return { body: view() };
    },
  });
  window.location.hash = '#/settings';
  render(<App />);
  expect(await screen.findByText('Server time (Europe/London)')).toBeInTheDocument();
  expect(screen.getByText(/Next backup:/)).toBeInTheDocument();
  const save = screen.getByRole('button', { name: 'Save' });
  expect(save).toBeDisabled();
  const time = screen.getByLabelText('Time');
  await userEvent.clear(time);
  await userEvent.type(time, '01:30');
  await userEvent.click(screen.getByRole('checkbox', { name: 'Sat' }));
  await userEvent.click(save);
  const put = calls.find((c) => c.key === 'PUT settings/schedule');
  expect(JSON.parse(String(put?.init?.body))).toEqual({
    enabled: true,
    time: '01:30',
    days: [0, 1, 2, 3, 4, 6],
  });
  await userEvent.click(screen.getByRole('switch', { name: 'Back up automatically' }));
  await userEvent.click(screen.getByRole('button', { name: 'Save' }));
  expect(await screen.findByText('Automatic backups are off.')).toBeInTheDocument();
});
