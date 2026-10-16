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
  await userEvent.click(screen.getByRole('link', { name: 'Back to status' }));
  expect(await screen.findByRole('button', { name: 'Run now' })).toBeInTheDocument();
});
