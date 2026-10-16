import { Button, Group, Text } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { type AuthState, useLogout } from '../api/auth';
import { api } from '../api/client';
import { AuthCard } from '../components/AuthCard';

interface Version {
  name: string;
  version: string;
}

/** Placeholder until the status page exists (phase 0.1, chunk 8). */
export function HomePage({ auth }: { auth: AuthState }) {
  const version = useQuery({ queryKey: ['version'], queryFn: () => api<Version>('version') });
  const logout = useLogout();

  return (
    <AuthCard
      title="BackupHaven"
      subtitle={version.data ? `Version ${version.data.version}` : undefined}
    >
      <Text ta="center">
        This is an early test build. It doesn&apos;t back anything up yet; backups arrive later in
        version 0.1.
      </Text>
      <Group justify="space-between">
        <Text size="sm" c="dimmed">
          {auth.method === 'bypass' ? 'Local network access' : `Logged in as ${auth.username}`}
        </Text>
        {auth.method === 'session' && (
          <Button
            variant="default"
            size="xs"
            loading={logout.isPending}
            onClick={() => logout.mutate()}
          >
            Log out
          </Button>
        )}
      </Group>
    </AuthCard>
  );
}
