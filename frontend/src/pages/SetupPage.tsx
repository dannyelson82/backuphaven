import { Alert, Button, PasswordInput, Stack, TextInput } from '@mantine/core';
import { useForm } from '@mantine/form';
import { useSetup } from '../api/auth';
import { errorMessage } from '../api/client';
import { AuthCard } from '../components/AuthCard';

export const MIN_PASSWORD = 10;

export function SetupPage() {
  const setup = useSetup();
  const form = useForm({
    initialValues: { username: 'admin', password: '', confirm: '' },
    validate: {
      username: (v) =>
        /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(v.trim())
          ? null
          : "Use letters, digits, '.', '_' or '-'",
      password: (v) =>
        v.length >= MIN_PASSWORD ? null : `Use at least ${MIN_PASSWORD} characters`,
      confirm: (v, values) => (v === values.password ? null : 'Passwords do not match'),
    },
  });

  return (
    <AuthCard
      title="Welcome to BackupHaven"
      subtitle="Create the admin account. You'll use it to log in from now on."
    >
      <form
        onSubmit={form.onSubmit(({ username, password }) =>
          setup.mutate({ username: username.trim(), password }),
        )}
      >
        <Stack>
          <TextInput label="Username" autoComplete="username" {...form.getInputProps('username')} />
          <PasswordInput
            label="Password"
            description={`At least ${MIN_PASSWORD} characters. A short sentence works well.`}
            autoComplete="new-password"
            {...form.getInputProps('password')}
          />
          <PasswordInput
            label="Confirm password"
            autoComplete="new-password"
            {...form.getInputProps('confirm')}
          />
          {setup.isError && <Alert color="red">{errorMessage(setup.error)}</Alert>}
          <Button type="submit" loading={setup.isPending}>
            Create account
          </Button>
        </Stack>
      </form>
    </AuthCard>
  );
}
