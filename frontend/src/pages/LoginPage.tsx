import { Alert, Button, Checkbox, PasswordInput, Stack, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useLogin } from '../api/auth';
import { errorMessage } from '../api/client';
import { AuthCard } from '../components/AuthCard';

export function LoginPage() {
  const login = useLogin();
  const form = useForm({
    initialValues: { username: '', password: '', remember: true },
    validate: {
      username: (v) => (v.trim() ? null : 'Enter your username'),
      password: (v) => (v ? null : 'Enter your password'),
    },
  });

  return (
    <AuthCard title="Log in to BackupHaven">
      {/* A real form with named fields, so password managers offer to fill and save. */}
      <form
        method="post"
        action="api/v1/auth/login"
        id="login"
        name="login"
        onSubmit={form.onSubmit(({ username, password, remember }) =>
          login.mutate({ username: username.trim(), password, remember }),
        )}
      >
        <Stack>
          <TextInput
            label="Username"
            id="username"
            name="username"
            type="text"
            autoComplete="username"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            autoFocus
            {...form.getInputProps('username')}
          />
          <PasswordInput
            label="Password"
            id="password"
            name="password"
            autoComplete="current-password"
            {...form.getInputProps('password')}
          />
          <Checkbox
            label="Keep me logged in"
            description="Stay logged in on this device until you log out or haven't visited for the time set in Security."
            name="remember"
            {...form.getInputProps('remember', { type: 'checkbox' })}
          />
          {login.isError && <Alert color="red">{errorMessage(login.error)}</Alert>}
          <Button type="submit" loading={login.isPending}>
            Log in
          </Button>
        </Stack>
      </form>
    </AuthCard>
  );
}
