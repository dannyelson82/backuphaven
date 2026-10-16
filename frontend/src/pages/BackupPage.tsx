import {
  Accordion,
  Alert,
  Anchor,
  Badge,
  Button,
  Group,
  Loader,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { type ContentApp, downloadUrl, useContents, useEntries } from '../api/backups';
import { errorMessage } from '../api/client';
import { formatBytes, formatDate } from '../format';

const RESULT: Record<string, { color: string; label: string }> = {
  ok: { color: 'green', label: 'OK' },
  warning: { color: 'yellow', label: 'Warnings' },
  failed: { color: 'red', label: 'Failed' },
  skipped: { color: 'gray', label: 'Skipped' },
};

function Files({ runId, app }: { runId: number; app: ContentApp }) {
  const entries = useEntries(runId, app.key);
  const [filter, setFilter] = useState('');
  if (entries.isPending) return <Loader size="sm" />;
  if (entries.isError) return <Alert color="red">{errorMessage(entries.error)}</Alert>;
  const term = filter.trim().toLowerCase();
  const shown = entries.data.filter((e) => !term || e.path.toLowerCase().includes(term));
  return (
    <Stack gap="xs">
      {entries.data.length > 20 && (
        <TextInput
          size="xs"
          placeholder="Find a file"
          aria-label={`Find a file in ${app.name}`}
          value={filter}
          onChange={(event) => setFilter(event.currentTarget.value)}
        />
      )}
      <Table.ScrollContainer minWidth={360}>
        <Table verticalSpacing={2} fz="xs">
          <Table.Tbody>
            {shown.slice(0, 500).map((entry) => (
              <Table.Tr key={entry.path}>
                <Table.Td style={{ overflowWrap: 'anywhere' }}>
                  {entry.kind === 'file' ? (
                    <Anchor href={downloadUrl.file(runId, app.key, entry.path)} size="xs">
                      {entry.path}
                    </Anchor>
                  ) : (
                    <Text size="xs" c="dimmed">
                      {entry.path} (link)
                    </Text>
                  )}
                </Table.Td>
                <Table.Td style={{ whiteSpace: 'nowrap' }}>{formatBytes(entry.size)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
      {shown.length > 500 && (
        <Text size="xs" c="dimmed">
          Showing 500 of {shown.length}. Use “Find a file” to narrow it down.
        </Text>
      )}
    </Stack>
  );
}

export function BackupPage() {
  const runId = Number(useParams().runId);
  const contents = useContents(runId);
  const [open, setOpen] = useState<string | null>(null);

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={3}>Backup</Title>
        <Anchor component={Link} to="/backups" size="sm">
          All backups
        </Anchor>
      </Group>
      {contents.isPending && <Loader />}
      {contents.isError && <Alert color="red">{errorMessage(contents.error)}</Alert>}
      {contents.data && (
        <>
          <Group justify="space-between" align="flex-start">
            <Stack gap={2}>
              <Text fw={500}>
                {contents.data.created ? formatDate(contents.data.created) : contents.data.archive}
              </Text>
              <Text size="xs" c="dimmed" style={{ overflowWrap: 'anywhere' }}>
                {contents.data.archive}
              </Text>
            </Stack>
            <Button component="a" href={downloadUrl.backup(runId)} variant="default" size="xs">
              Download backup
            </Button>
          </Group>
          <Text size="sm" c="dimmed">
            Open an app to see its files. Click a file to download it, or download a whole app as
            its own file.
          </Text>
          <Accordion variant="separated" value={open} onChange={setOpen}>
            {contents.data.apps.map((app) => {
              const result = RESULT[app.status] ?? { color: 'gray', label: app.status };
              return (
                <Accordion.Item key={app.name} value={app.name}>
                  <Accordion.Control disabled={!app.key}>
                    <Group gap="xs">
                      <Text fw={500}>{app.name}</Text>
                      <Badge size="sm" color={result.color}>
                        {result.label}
                      </Badge>
                      <Text size="xs" c="dimmed">
                        {app.files} files · {formatBytes(app.bytes)}
                        {app.databases.length > 0 && ` · ${app.databases.length} databases`}
                      </Text>
                    </Group>
                  </Accordion.Control>
                  <Accordion.Panel>
                    <Stack gap="xs">
                      <Group>
                        <Button
                          component={Link}
                          to={`/restore/${runId}/${encodeURIComponent(app.name)}`}
                          size="xs"
                          color="orange"
                          variant="light"
                        >
                          Restore {app.name}…
                        </Button>
                        <Button
                          component="a"
                          href={downloadUrl.app(runId, app.key)}
                          size="xs"
                          variant="default"
                        >
                          Download {app.name}
                        </Button>
                      </Group>
                      {open === app.name && <Files runId={runId} app={app} />}
                    </Stack>
                  </Accordion.Panel>
                </Accordion.Item>
              );
            })}
          </Accordion>
        </>
      )}
    </Stack>
  );
}
