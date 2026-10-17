import { Alert, Anchor, Badge, Card, Group, Loader, Stack, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { api, errorMessage } from '../api/client';

interface DockerStatus {
  configured: boolean;
  address: string | null;
  ok: boolean;
  error: string | null;
  containers: number;
}

export function DockerSettings() {
  const status = useQuery({
    queryKey: ['settings', 'docker'],
    queryFn: () => api<DockerStatus>('settings/docker'),
  });
  const data = status.data;
  return (
    <Card withBorder>
      <Stack gap="xs">
        <Group gap="xs">
          <Title order={4}>Containers (socket proxy)</Title>
          {data &&
            (data.ok ? (
              <Badge color="green" variant="light">
                Connected
              </Badge>
            ) : data.configured ? (
              <Badge color="red" variant="light">
                Not answering
              </Badge>
            ) : (
              <Badge color="gray" variant="light">
                Not set up
              </Badge>
            ))}
        </Group>
        {status.isPending && <Loader size="sm" />}
        {status.isError && <Alert color="red">{errorMessage(status.error)}</Alert>}
        {data && !data.configured && (
          <Text size="sm" c="dimmed">
            Optional. With a small helper container (a socket proxy that only allows listing,
            stopping and starting containers), BackupHaven knows which containers exist, can stop an
            app during its backup, and stops and starts apps itself when restoring. See the guide:{' '}
            <Anchor href="https://github.com/dannyelson82/backuphaven/blob/main/docs/guide/stop-mode.md">
              Stop mode and the socket proxy
            </Anchor>
            .
          </Text>
        )}
        {data?.ok && (
          <Text size="sm" c="dimmed">
            {data.address}: {data.containers} containers.
          </Text>
        )}
        {data?.configured && !data.ok && <Alert color="red">{data.error}</Alert>}
      </Stack>
    </Card>
  );
}
