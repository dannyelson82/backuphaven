import {
  Alert,
  Badge,
  Button,
  Card,
  Code,
  Container,
  Group,
  Image,
  List,
  Loader,
  Progress as Bar,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { type AuthState, useLogout } from '../api/auth';
import { errorMessage } from '../api/client';
import {
  type DbCheck,
  type Progress,
  type RunApp,
  type RunStatus,
  useRun,
  useRunNow,
  useStatus,
} from '../api/status';
import { fileName, formatBytes, formatDate, formatDuration, timeAgo } from '../format';

const STATUS: Record<RunStatus, { color: string; label: string }> = {
  running: { color: 'blue', label: 'Running' },
  ok: { color: 'green', label: 'OK' },
  warning: { color: 'yellow', label: 'Warnings' },
  failed: { color: 'red', label: 'Failed' },
};

const DB_STATUS: Record<DbCheck['status'], { color: string; label: string }> = {
  ok: { color: 'green', label: 'Healthy' },
  damaged: { color: 'red', label: 'Damaged' },
  unchecked: { color: 'yellow', label: 'Not checked' },
  failed: { color: 'red', label: "Couldn't copy" },
};

function StatusBadge({ status }: { status: RunStatus }) {
  return <Badge color={STATUS[status].color}>{STATUS[status].label}</Badge>;
}

function Running({ progress }: { progress: Progress }) {
  const percent = progress.apps_total ? (progress.apps_done / progress.apps_total) * 100 : 0;
  return (
    <Stack gap="xs">
      <Group justify="space-between">
        <Text fw={500}>
          {progress.phase === 'verifying'
            ? 'Checking the backup file…'
            : progress.app
              ? `Backing up ${progress.app}`
              : 'Starting…'}
        </Text>
        <Text size="sm" c="dimmed">
          {progress.apps_done} of {progress.apps_total} apps · {progress.files} files ·{' '}
          {formatBytes(progress.bytes)} · {formatDuration(progress.seconds)}
        </Text>
      </Group>
      <Bar value={percent} animated aria-label="Backup progress" />
    </Stack>
  );
}

function DatabaseHealth({ checks }: { checks: DbCheck[] }) {
  if (!checks.length) return null;
  return (
    <Card withBorder>
      <Title order={4} mb="sm">
        Database health
      </Title>
      <Table.ScrollContainer minWidth={420}>
        <Table verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>App</Table.Th>
              <Table.Th>Database</Table.Th>
              <Table.Th>Size</Table.Th>
              <Table.Th>Check</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {checks.map((check) => (
              <Table.Tr key={`${check.app}/${check.path}`}>
                <Table.Td>{check.app}</Table.Td>
                <Table.Td>
                  <Text size="sm" title={check.path}>
                    {fileName(check.path)}
                  </Text>
                  {check.status !== 'ok' && check.messages.length > 0 && (
                    <Text size="xs" c="red">
                      {check.messages[0]}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>{formatBytes(check.size)}</Table.Td>
                <Table.Td>
                  <Group gap={6}>
                    <Badge color={DB_STATUS[check.status].color} variant="light">
                      {DB_STATUS[check.status].label}
                    </Badge>
                    {check.status === 'ok' && check.mode === 'quick' && (
                      <Text size="xs" c="dimmed" title="Structure checked; full check not possible">
                        quick check
                      </Text>
                    )}
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Card>
  );
}

function AppResults({ apps }: { apps: RunApp[] }) {
  return (
    <Card withBorder>
      <Title order={4} mb="sm">
        Apps in the last backup
      </Title>
      <Stack gap="sm">
        {apps.map((app) => (
          <Stack key={app.app} gap={2}>
            <Group justify="space-between">
              <Group gap="xs">
                <Text fw={500}>{app.app}</Text>
                <StatusBadge status={app.status} />
              </Group>
              <Text size="sm" c="dimmed">
                {app.files} files · {formatBytes(app.bytes)} · plugin {app.plugin}
              </Text>
            </Group>
            {[...app.errors, ...app.warnings].length > 0 && (
              <List size="sm" c={app.errors.length ? 'red' : 'yellow.8'}>
                {[...app.errors, ...app.warnings].slice(0, 5).map((text) => (
                  <List.Item key={text}>{text}</List.Item>
                ))}
              </List>
            )}
          </Stack>
        ))}
      </Stack>
    </Card>
  );
}

export function StatusPage({ auth }: { auth: AuthState }) {
  const status = useStatus();
  const runNow = useRunNow();
  const logout = useLogout();
  const lastRun = status.data?.last_run ?? null;
  const detail = useRun(lastRun?.id);

  return (
    <Container size="md" py="md">
      <Stack gap="md">
        <Group justify="space-between">
          <Group gap="sm">
            <Image src="./favicon.svg" w={36} h={36} alt="" />
            <Title order={2}>BackupHaven</Title>
          </Group>
          <Group gap="xs">
            <Text size="sm" c="dimmed">
              {auth.method === 'bypass' ? 'Local network access' : auth.username}
            </Text>
            {auth.method === 'session' && (
              <Button variant="default" size="xs" onClick={() => logout.mutate()}>
                Log out
              </Button>
            )}
          </Group>
        </Group>

        {status.isPending && <Loader />}
        {status.isError && <Alert color="red">{errorMessage(status.error)}</Alert>}

        {status.data && !status.data.templates_found && (
          <Alert color="yellow" title="BackupHaven can't see your containers">
            Add the <b>Unraid flash (read only)</b> path (<Code>/boot</Code>) to BackupHaven&apos;s
            container settings.
          </Alert>
        )}
        {status.data && status.data.missing_shares.length > 0 && (
          <Alert color="yellow" title="Some app data is on a share BackupHaven can't see">
            <List size="sm">
              {status.data.missing_shares.map((m) => (
                <List.Item key={`${m.app}${m.target}`}>
                  {m.app}: add <Code>/mnt/user/{m.share}</Code> as a path, with the same path on
                  both sides.
                </List.Item>
              ))}
            </List>
          </Alert>
        )}
        {lastRun?.status === 'failed' && lastRun.error && (
          <Alert color="red" title="The last backup failed">
            {lastRun.error}
          </Alert>
        )}

        {status.data && (
          <Card withBorder>
            <Stack gap="sm">
              <Group justify="space-between" align="flex-start">
                <Stack gap={4}>
                  <Title order={4}>Last backup</Title>
                  {lastRun ? (
                    <Group gap="xs">
                      <StatusBadge status={lastRun.status} />
                      <Text size="sm" title={formatDate(lastRun.started_at)}>
                        {timeAgo(lastRun.started_at)}
                      </Text>
                      {lastRun.size !== null && (
                        <Text size="sm" c="dimmed">
                          {formatBytes(lastRun.size)} · {lastRun.summary?.apps ?? 0} apps ·{' '}
                          {formatDuration(lastRun.summary?.seconds)}
                        </Text>
                      )}
                    </Group>
                  ) : (
                    <Text size="sm" c="dimmed">
                      No backups yet.
                    </Text>
                  )}
                </Stack>
                <Button
                  onClick={() => runNow.mutate()}
                  loading={runNow.isPending}
                  disabled={status.data.running !== null}
                >
                  Run now
                </Button>
              </Group>
              {runNow.isError && <Alert color="red">{errorMessage(runNow.error)}</Alert>}
              {status.data.running && <Running progress={status.data.running} />}
              {lastRun?.archive && (
                <Text size="xs" c="dimmed">
                  {lastRun.archive}
                  {lastRun.verified && ' · checked after writing'}
                </Text>
              )}
            </Stack>
          </Card>
        )}

        {status.data && (
          <SimpleGrid cols={{ base: 1, sm: 3 }}>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed">
                Apps found
              </Text>
              <Text fw={600}>{status.data.apps}</Text>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed">
                Data shares
              </Text>
              <Text fw={600}>{status.data.data_shares.join(', ') || 'none'}</Text>
            </Card>
            <Card withBorder padding="sm">
              <Text size="xs" c="dimmed">
                Free on the backups disk
              </Text>
              <Text fw={600}>{formatBytes(status.data.backups_free_bytes)}</Text>
            </Card>
          </SimpleGrid>
        )}

        {status.data && <DatabaseHealth checks={status.data.database_health} />}
        {detail.data && detail.data.apps.length > 0 && <AppResults apps={detail.data.apps} />}
      </Stack>
    </Container>
  );
}
