import {
  Alert,
  Anchor,
  Button,
  Card,
  Checkbox,
  Code,
  Group,
  List,
  Loader,
  Stack,
  Stepper,
  Text,
  Title,
} from '@mantine/core';
import { useState } from 'react';
import { Link, useParams } from 'react-router';
import { useContents } from '../api/backups';
import { errorMessage } from '../api/client';
import {
  useCheckRestore,
  useRestore,
  useRestoreProgress,
  useStartRestore,
  useUndoRestore,
} from '../api/restores';
import { formatDate } from '../format';

export function RestorePage() {
  const params = useParams();
  const runId = Number(params.runId);
  const app = params.app ?? '';
  const contents = useContents(runId);
  const check = useCheckRestore();
  const start = useStartRestore();
  const undo = useUndoRestore();
  const [confirmed, setConfirmed] = useState(false);
  const restoreId = start.data?.restore_id ?? null;
  const restore = useRestore(restoreId);
  const running = restoreId !== null && (!restore.data || restore.data.status === 'running');
  const progress = useRestoreProgress(running);

  const created = contents.data?.created;
  const stopped = check.data?.stopped === true;
  const finished = restore.data && restore.data.status !== 'running' ? restore.data : null;
  const step = finished ? 2 : restoreId !== null || stopped ? 1 : 0;

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={3}>Restore {app}</Title>
        <Anchor component={Link} to={`/backups/${runId}`} size="sm">
          Back to the backup
        </Anchor>
      </Group>
      {contents.isPending && <Loader />}
      {contents.isError && <Alert color="red">{errorMessage(contents.error)}</Alert>}
      {contents.data && (
        <Text size="sm">
          Puts {app}&apos;s files back as they were in the backup from{' '}
          <b>{created ? formatDate(created) : contents.data.archive}</b>. Its current files are kept
          aside for 7 days, so you can undo this.
        </Text>
      )}
      <Stepper active={step} orientation="vertical" size="sm">
        <Stepper.Step label="Stop the container" description="In Unraid">
          <Card withBorder>
            <Stack gap="xs">
              <Text size="sm">
                In Unraid, go to <b>Docker</b>, click <b>{app}</b> and choose <b>Stop</b>. Then
                press <b>Check it&apos;s stopped</b>: BackupHaven watches its files for a few
                seconds.
              </Text>
              <Group>
                <Button
                  size="xs"
                  onClick={() => check.mutate({ run_id: runId, app })}
                  loading={check.isPending}
                  disabled={restoreId !== null}
                >
                  Check it&apos;s stopped
                </Button>
              </Group>
              {check.isError && <Alert color="red">{errorMessage(check.error)}</Alert>}
              {check.data && !check.data.stopped && (
                <Alert color="yellow" title={`${app} still seems to be running`}>
                  These files changed while BackupHaven watched:
                  <List size="xs">
                    {check.data.changing.slice(0, 5).map((path) => (
                      <List.Item key={path}>
                        <Code>{path}</Code>
                      </List.Item>
                    ))}
                  </List>
                  Stop the container and check again.
                </Alert>
              )}
            </Stack>
          </Card>
        </Stepper.Step>
        <Stepper.Step label="Restore" description="Replace the files">
          <Card withBorder>
            <Stack gap="xs">
              {check.data?.stopped && (
                <>
                  <Text size="sm">These folders will be replaced:</Text>
                  <List size="sm">
                    {check.data.folders
                      .filter((f) => f.in_backup)
                      .map((f) => (
                        <List.Item key={f.host}>
                          <Code>{f.host}</Code>
                        </List.Item>
                      ))}
                  </List>
                  <Checkbox
                    label={`${app} is stopped, and I want to replace its files with the backup`}
                    checked={confirmed}
                    onChange={(event) => setConfirmed(event.currentTarget.checked)}
                    disabled={restoreId !== null}
                  />
                  <Group>
                    <Button
                      size="xs"
                      color="orange"
                      disabled={!confirmed || restoreId !== null}
                      loading={start.isPending}
                      onClick={() => start.mutate({ run_id: runId, app })}
                    >
                      Restore {app}
                    </Button>
                  </Group>
                </>
              )}
              {start.isError && <Alert color="red">{errorMessage(start.error)}</Alert>}
              {running && (
                <Group gap="xs">
                  <Loader size="xs" />
                  <Text size="sm">{progress.data?.phase ?? 'Starting'}…</Text>
                </Group>
              )}
            </Stack>
          </Card>
        </Stepper.Step>
        <Stepper.Step label="Start it again" description="In Unraid">
          {finished && (
            <Card withBorder>
              <Stack gap="xs">
                {finished.status === 'done' && (
                  <>
                    <Alert color="green" title={`${app} is restored`}>
                      In Unraid, go to <b>Docker</b>, click <b>{app}</b> and choose <b>Start</b>.
                      {finished.detail?.folders?.some((f) => f.moved_back.length > 0) &&
                        ' Files the backup leaves out on purpose (such as caches and posters) were kept.'}
                    </Alert>
                    <Text size="sm">
                      If it&apos;s worse than before, stop {app} again and press <b>Undo</b>
                      {finished.undo_until &&
                        ` (possible until ${formatDate(finished.undo_until)})`}
                      .
                    </Text>
                    <Group>
                      <Button
                        size="xs"
                        variant="default"
                        loading={undo.isPending}
                        onClick={() => undo.mutate(finished.id)}
                      >
                        Undo
                      </Button>
                    </Group>
                  </>
                )}
                {finished.status === 'failed' && (
                  <Alert color="red" title="The restore didn't finish">
                    {finished.error}. {app}&apos;s files were put back as they were.
                  </Alert>
                )}
                {undo.isError && <Alert color="red">{errorMessage(undo.error)}</Alert>}
                {undo.isSuccess && (
                  <Alert color="blue" title="Undone">
                    {app}&apos;s files are back as they were before the restore. Start it again in
                    Unraid.
                  </Alert>
                )}
              </Stack>
            </Card>
          )}
        </Stepper.Step>
      </Stepper>
    </Stack>
  );
}
