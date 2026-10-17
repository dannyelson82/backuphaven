import {
  Alert,
  Button,
  Group,
  Loader,
  Modal,
  NumberInput,
  Stack,
  Text,
  TextInput,
} from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, errorMessage } from '../api/client';

interface ExternalDatabase {
  id: string;
  engine: 'mariadb' | 'postgres';
  host: string | null;
  port: number | null;
  database: string | null;
  user: string | null;
  from_template: boolean;
  problem: string | null;
}

const ENGINE = { mariadb: 'MariaDB', postgres: 'PostgreSQL' };

function One({ app, db }: { app: string; db: ExternalDatabase }) {
  const queryClient = useQueryClient();
  const key = ['apps', app, 'databases'];
  const [host, setHost] = useState(db.host ?? '');
  const [port, setPort] = useState<number | string>(
    db.port ?? (db.engine === 'mariadb' ? 3306 : 5432),
  );
  const save = useMutation({
    mutationFn: (body: { host: string; port: number } | null) =>
      api<ExternalDatabase[]>(`apps/${encodeURIComponent(app)}/databases/${db.id}`, {
        method: body ? 'PUT' : 'DELETE',
        ...(body ? { body: JSON.stringify(body) } : {}),
      }),
    onSuccess: (data) => queryClient.setQueryData(key, data),
  });
  return (
    <Stack gap="xs">
      <Text size="sm">
        <b>{ENGINE[db.engine]}</b>
        {db.database && (
          <>
            {' '}
            database <b>{db.database}</b>
          </>
        )}
        {db.user && <>, user {db.user}</>}
      </Text>
      {db.problem && <Alert color="yellow">{db.problem}</Alert>}
      <Text size="xs" c="dimmed">
        {db.from_template
          ? "Address from the app's Unraid template."
          : 'Address set here (the template says something else).'}{' '}
        The password always comes from the template.
      </Text>
      <Group align="flex-end" gap="xs">
        <TextInput
          label="Address"
          description="Server name or IP address"
          value={host}
          onChange={(event) => setHost(event.currentTarget.value)}
        />
        <NumberInput label="Port" value={port} onChange={setPort} min={1} max={65535} w={110} />
        <Button
          size="sm"
          loading={save.isPending}
          onClick={() => save.mutate({ host: host.trim(), port: Number(port) })}
        >
          Save
        </Button>
        {!db.from_template && (
          <Button size="sm" variant="subtle" onClick={() => save.mutate(null)}>
            Use the template&apos;s
          </Button>
        )}
      </Group>
      {save.isError && <Alert color="red">{errorMessage(save.error)}</Alert>}
    </Stack>
  );
}

export function DatabaseAddress({ app, onClose }: { app: string; onClose: () => void }) {
  const databases = useQuery({
    queryKey: ['apps', app, 'databases'],
    queryFn: () => api<ExternalDatabase[]>(`apps/${encodeURIComponent(app)}/databases`),
  });
  return (
    <Modal opened onClose={onClose} title={`${app}: database`}>
      <Stack gap="md">
        <Text size="sm" c="dimmed">
          {app} keeps its data in a database server in another container. BackupHaven dumps just
          {` ${app}`}&apos;s database while everything keeps running, and loads it back on restore.
          If the template&apos;s address can&apos;t be reached from BackupHaven (a container name on
          another network), enter the server&apos;s IP address and the port it publishes.
        </Text>
        {databases.isPending && <Loader size="sm" />}
        {databases.isError && <Alert color="red">{errorMessage(databases.error)}</Alert>}
        {databases.data?.map((db) => (
          <One key={db.id} app={app} db={db} />
        ))}
      </Stack>
    </Modal>
  );
}
