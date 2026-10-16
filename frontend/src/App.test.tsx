import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from './App';
import { mockApi, state } from './test/mockApi';

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = '';
});

describe('first run', () => {
  it('shows the setup page when no admin exists', async () => {
    mockApi({ 'GET auth/state': state({ setup_required: true }) });
    render(<App />);
    expect(await screen.findByText('Welcome to BackupHaven')).toBeInTheDocument();
  });

  it('checks the passwords match before sending anything', async () => {
    const calls = mockApi({ 'GET auth/state': state({ setup_required: true }) });
    render(<App />);
    await userEvent.type(await screen.findByLabelText('Password'), 'long enough password');
    await userEvent.type(screen.getByLabelText('Confirm password'), 'something else entirely');
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(await screen.findByText('Passwords do not match')).toBeInTheDocument();
    expect(calls.some((c) => c.key === 'POST setup')).toBe(false);
  });

  it('creates the admin and sends the CSRF token', async () => {
    let created = false;
    const calls = mockApi({
      'GET auth/state': () =>
        created
          ? state({ authenticated: true, username: 'admin', method: 'session' })
          : state({ setup_required: true }),
      'POST setup': () => {
        created = true;
        return { status: 201, body: {} };
      },
      'GET version': { body: { name: 'BackupHaven', version: '0.0.1' } },
    });
    render(<App />);
    await userEvent.type(await screen.findByLabelText('Password'), 'long enough password');
    await userEvent.type(screen.getByLabelText('Confirm password'), 'long enough password');
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(await screen.findByText('Logged in as admin')).toBeInTheDocument();
    expect(await screen.findByText('Version 0.0.1')).toBeInTheDocument();
    const post = calls.find((c) => c.key === 'POST setup');
    expect(new Headers(post?.init?.headers).get('X-CSRF-Token')).toBe('csrf-123');
  });
});

it('says when the server is unreachable', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.reject(new Error('offline'))),
  );
  render(<App />);
  expect(await screen.findByText('BackupHaven is not responding')).toBeInTheDocument();
});

it('logs out', async () => {
  let loggedIn = true;
  const calls = mockApi({
    'GET auth/state': () =>
      loggedIn ? state({ authenticated: true, username: 'admin', method: 'session' }) : state(),
    'GET version': { body: { name: 'BackupHaven', version: '0.0.1' } },
    'POST auth/logout': () => {
      loggedIn = false;
      return { status: 204 };
    },
  });
  render(<App />);
  await userEvent.click(await screen.findByRole('button', { name: 'Log out' }));
  expect(await screen.findByRole('button', { name: 'Log in' })).toBeInTheDocument();
  expect(calls.some((c) => c.key === 'POST auth/logout')).toBe(true);
});
