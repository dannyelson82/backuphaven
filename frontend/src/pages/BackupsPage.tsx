import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Modal,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { useState } from 'react';
import { Link } from 'react-router';
import { type Backup, useBackups, useDeleteBackup } from '../api/backups';
import { errorMessage } from '../api/client';
import { formatBytes, formatDate, timeAgo } from '../format';

const RESULT: Record<string, { color: string; label: string }> = {
  ok: { color: 'green', label: 'OK' },
  warning: { color: 'yellow', label: 'Warnings' },
  failed: { color: 'red', label: 'Failed' },
};

/** "nightly", "weekly", "last good copy of plex/…" → readable text. */
function keptBecause(reasons: string[]): string {
  if (!reasons.length) return 'Will be deleted after the next backup';
  const nice = reasons.map((r) =>
    r.startsWith('last good copy of ')
      ? `last healthy copy of ${r.slice('last good copy of '.length).split('/')[0]}'s database`
      : r,
  );
  return `Kept: ${[...new Set(nice)].join(', ')}`;
}

function BackupRow({ backup, onDelete }: { backup: Backup; onDelete: () => void }) {
  const result = RESULT[backup.status] ?? { color: 'gray', label: backup.status };
  const apps = backup.summary?.apps ?? 0;
  return (
    <Card withBorder padding="sm">
      <Group justify="space-between" wrap="nowrap" align="flex-start">
        <Stack gap={4} style={{ minWidth: 0 }}>
          <Group gap="xs">
            <Text fw={500} title={formatDate(backup.started_at)}>
              {formatDate(backup.started_at)}
            </Text>
            <Badge color={result.color}>{result.label}</Badge>
            {backup.summary?.chosen && (
              <Badge variant="light" color="gray">
                Chosen apps
              </Badge>
            )}
            {!backup.on_disk && (
              <Badge variant="light" color="red">
                File missing
              </Badge>
            )}
          </Group>
          <Text size="sm" c="dimmed">
            {timeAgo(backup.started_at)} · {formatBytes(backup.size)} · {apps}{' '}
            {apps === 1 ? 'app' : 'apps'}
            {backup.databases > 0 &&
              ` · ${backup.healthy} of ${backup.databases} databases healthy`}
            {backup.verified && ' · checked after writing'}
          </Text>
          {backup.summary?.only && backup.summary.chosen && (
            <Text size="xs" c="dimmed">
              {backup.summary.only.join(', ')}
            </Text>
          )}
          <Text size="xs" c={backup.keep_reasons.length ? 'dimmed' : 'yellow.8'}>
            {keptBecause(backup.keep_reasons)}
          </Text>
          <Text size="xs" c="dimmed" style={{ overflowWrap: 'anywhere' }}>
            {backup.archive}
          </Text>
        </Stack>
        <Group gap="xs" wrap="nowrap" style={{ flexShrink: 0 }}>
          <Button
            size="xs"
            variant="default"
            component={Link}
            to={`/backups/${backup.run_id}`}
            disabled={!backup.on_disk}
          >
            Open
          </Button>
          <Button
            size="xs"
            variant="default"
            color="red"
            disabled={backup.protected}
            title={backup.protected ? 'Protected: see why it is kept' : undefined}
            onClick={onDelete}
          >
            Delete
          </Button>
        </Group>
      </Group>
    </Card>
  );
}

export function BackupsPage() {
  const backups = useBackups();
  const remove = useDeleteBackup();
  const [confirm, setConfirm] = useState<Backup | null>(null);
  const total = (backups.data ?? []).reduce((sum, b) => sum + (b.size ?? 0), 0);

  return (
    <Stack gap="md">
      <Title order={3}>Backups</Title>
      {backups.data && (
        <Text size="sm" c="dimmed">
          {backups.data.length} {backups.data.length === 1 ? 'backup' : 'backups'},{' '}
          {formatBytes(total)} in total. Old backups are cleared out after each backup, following
          Settings → Keeping backups. Restoring comes in a later version.
        </Text>
      )}
      {backups.isPending && <Loader />}
      {backups.isError && <Alert color="red">{errorMessage(backups.error)}</Alert>}
      {remove.isError && <Alert color="red">{errorMessage(remove.error)}</Alert>}
      {backups.data?.length === 0 && (
        <Text size="sm" c="dimmed">
          No backups yet.
        </Text>
      )}
      <Stack gap="xs">
        {backups.data?.map((backup) => (
          <BackupRow key={backup.run_id} backup={backup} onDelete={() => setConfirm(backup)} />
        ))}
      </Stack>
      <Modal opened={confirm !== null} onClose={() => setConfirm(null)} title="Delete this backup?">
        {confirm && (
          <Stack gap="sm">
            <Text size="sm">
              The backup from <b>{formatDate(confirm.started_at)}</b> ({formatBytes(confirm.size)})
              will be deleted from the backups folder. This can&apos;t be undone.
            </Text>
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setConfirm(null)}>
                Keep it
              </Button>
              <Button
                color="red"
                loading={remove.isPending}
                onClick={() => remove.mutate(confirm.run_id, { onSuccess: () => setConfirm(null) })}
              >
                Delete backup
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}
