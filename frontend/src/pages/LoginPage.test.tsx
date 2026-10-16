import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { App } from '../App';
import { mockApi, state } from '../test/mockApi';

it('has a named login form and sends Keep me logged in', async () => {
  window.location.hash = '#/';
  const calls = mockApi({
    'GET auth/state': state(),
    'POST auth/login': { body: state({ authenticated: true }).body },
  });
  render(<App />);
  const username = await screen.findByLabelText('Username');
  const password = screen.getByLabelText('Password');
  // Password managers look for these.
  expect(username).toHaveAttribute('name', 'username');
  expect(username).toHaveAttribute('autocomplete', 'username');
  expect(password).toHaveAttribute('name', 'password');
  expect(password).toHaveAttribute('autocomplete', 'current-password');
  expect(username.closest('form')).toHaveAttribute('method', 'post');

  const keep = screen.getByLabelText('Keep me logged in');
  expect(keep).toBeChecked();
  await userEvent.click(keep);
  await userEvent.type(username, ' Admin ');
  await userEvent.type(password, 'a long password');
  await userEvent.click(screen.getByRole('button', { name: 'Log in' }));
  const sent = calls.find((c) => c.key === 'POST auth/login');
  expect(JSON.parse(String(sent?.init?.body))).toEqual({
    username: 'Admin',
    password: 'a long password',
    remember: false,
  });
});
