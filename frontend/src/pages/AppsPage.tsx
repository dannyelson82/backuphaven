import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Modal,
  SegmentedControl,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useState } from 'react';
import { useNavigate } from 'react-router';
import { useBackups } from '../api/backups';
import { errorMessage } from '../api/client';
import {
  type DiscoveredApp,
  type RunStatus,
  useDiscoveredApps,
  useScheduleApps,
} from '../api/status';
import { formatBytes, formatDate, timeAgo } from '../format';

const RESULT: Partial<Record<RunStatus, { color: string; label: string }>> = {
  ok: { color: 'green', label: 'OK' },
  warning: { color: 'yellow', label: 'Warnings' },
  failed: { color: 'red', label: 'Failed' },
};

type Show = 'scheduled' | 'all';

function RestoreChoice({ app, onClose }: { app: string; onClose: () => void }) {
  const backups = useBackups();
  const navigate = useNavigate();
  const holding = (backups.data ?? []).filter((b) =>
    b.apps.some((name) => name.toLowerCase() === app.toLowerCase()),
  );
  return (
    <Modal opened onClose={onClose} title={`Restore ${app} from…`}>
      <Stack gap="xs">
        {backups.isPending && <Loader size="sm" />}
        {backups.data && holding.length === 0 && (
          <Text size="sm" c="dimmed">
            No backup holds {app} yet.
          </Text>
        )}
        {holding.map((b) => (
          <Button
            key={b.run_id}
            variant="default"
            justify="space-between"
            onClick={() => navigate(`/restore/${b.run_id}/${encodeURIComponent(app)}`)}
            rightSection={
              <Text size="xs" c="dimmed">
                {timeAgo(b.started_at)}
              </Text>
            }
          >
            {formatDate(b.started_at)}
          </Button>
        ))}
      </Stack>
    </Modal>
  );
}

function AppRow({
  app,
  busy,
  onToggle,
  onRestore,
}: {
  app: DiscoveredApp;
  busy: boolean;
  onToggle: () => void;
  onRestore: () => void;
}) {
  const last = app.last_backup;
  const result = last ? RESULT[last.status] : undefined;
  return (
    <Card withBorder padding="sm">
      <Group justify="space-between" wrap="nowrap" align="flex-start">
        <Stack gap={4} style={{ minWidth: 0 }}>
          <Group gap="xs">
            <Text fw={500}>{app.name}</Text>
            {app.scheduled && (
              <Badge variant="light" color="violet">
                In the backup schedule
              </Badge>
            )}
            {app.not_installed && (
              <Badge variant="light" color="gray">
                Not installed
              </Badge>
            )}
            {app.missing.length > 0 && (
              <Badge variant="light" color="yellow">
                Share not added
              </Badge>
            )}
          </Group>
          <Text size="xs" c="dimmed" style={{ overflowWrap: 'anywhere' }}>
            Plugin {app.plugin}
            {app.folders.length > 0 && ` · ${app.folders.map((f) => f.host).join(', ')}`}
          </Text>
          {app.missing.length > 0 && (
            <Text size="xs" c="yellow.8">
              Data on the {app.missing[0].share} share, which BackupHaven can&apos;t see.
            </Text>
          )}
          {last && (
            <Group gap={6}>
              <Text size="xs" c="dimmed">
                Last backup {timeAgo(last.started_at)} · {formatBytes(last.bytes)}
              </Text>
              {result && (
                <Badge size="xs" color={result.color} variant="light">
                  {result.label}
                </Badge>
              )}
            </Group>
          )}
        </Stack>
        <Stack gap={4} style={{ flexShrink: 0 }} align="flex-end">
          <Button
            size="xs"
            variant={app.scheduled ? 'default' : 'filled'}
            onClick={onToggle}
            loading={busy}
          >
            {app.scheduled ? 'Remove' : 'Add to backup schedule'}
          </Button>
          {last && (
            <Button size="xs" variant="subtle" color="orange" onClick={onRestore}>
              Restore…
            </Button>
          )}
        </Stack>
      </Group>
    </Card>
  );
}

export function AppsPage() {
  const discovered = useDiscoveredApps(true);
  const save = useScheduleApps();
  const [show, setShow] = useState<Show | null>(null);
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [restoring, setRestoring] = useState<string | null>(null);

  const scheduled = discovered.data?.scheduled ?? [];
  // Until the owner chooses, show everything when nothing is scheduled yet.
  const view: Show = show ?? (scheduled.length ? 'scheduled' : 'all');
  const names = new Set((discovered.data?.apps ?? []).map((a) => a.name.toLowerCase()));
  const gone = scheduled.filter((name) => !names.has(name.toLowerCase()));
  const term = search.trim().toLowerCase();
  const apps = (discovered.data?.apps ?? [])
    .filter((app) => view === 'all' || app.scheduled)
    .filter((app) => !term || app.name.toLowerCase().includes(term))
    .sort(
      (a, b) =>
        Number(!!a.not_installed) - Number(!!b.not_installed) ||
        a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }),
    );

  const change = (name: string, add: boolean) => {
    setBusy(name);
    const next = add
      ? [...scheduled, name]
      : scheduled.filter((n) => n.toLowerCase() !== name.toLowerCase());
    save.mutate(next, { onSettled: () => setBusy(null) });
  };

  return (
    <Stack gap="md">
      <Title order={3}>Apps</Title>
      <Text size="sm" c="dimmed">
        Every container BackupHaven found in Unraid&apos;s templates. Only apps in the{' '}
        <b>backup schedule</b> are backed up by Run now and the nightly backup.
      </Text>
      {discovered.isPending && <Loader />}
      {discovered.isError && <Alert color="red">{errorMessage(discovered.error)}</Alert>}
      {save.isError && <Alert color="red">{errorMessage(save.error)}</Alert>}
      {discovered.data && (
        <>
          <Group justify="space-between">
            <SegmentedControl
              value={view}
              onChange={(value) => setShow(value as Show)}
              data={[
                { value: 'scheduled', label: `In the backup schedule (${scheduled.length})` },
                { value: 'all', label: `All apps (${discovered.data.apps.length})` },
              ]}
            />
            <TextInput
              placeholder="Search"
              aria-label="Search apps"
              value={search}
              onChange={(event) => setSearch(event.currentTarget.value)}
            />
          </Group>
          {gone.map((name) => (
            <Alert key={name} color="yellow" title={`${name} is no longer found`}>
              <Group justify="space-between">
                <Text size="sm">
                  It&apos;s in the backup schedule, but Unraid has no template with this name any
                  more. Backups skip it.
                </Text>
                <Button size="xs" variant="default" onClick={() => change(name, false)}>
                  Remove
                </Button>
              </Group>
            </Alert>
          ))}
          {apps.length === 0 && (
            <Text size="sm" c="dimmed">
              {view === 'scheduled'
                ? 'Nothing is in the backup schedule yet. Choose “All apps” to add some.'
                : 'No apps match.'}
            </Text>
          )}
          <Stack gap="xs">
            {apps.map((app) => (
              <AppRow
                key={app.name}
                app={app}
                busy={busy === app.name}
                onToggle={() => change(app.name, !app.scheduled)}
                onRestore={() => setRestoring(app.name)}
              />
            ))}
          </Stack>
        </>
      )}
      {restoring && <RestoreChoice app={restoring} onClose={() => setRestoring(null)} />}
    </Stack>
  );
}
