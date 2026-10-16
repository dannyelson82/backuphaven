import {
  Alert,
  Anchor,
  Button,
  Card,
  Chip,
  Group,
  Loader,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { errorMessage } from '../api/client';
import { type ScheduleSettings as Settings, useSaveSchedule, useSchedule } from '../api/schedule';
import { formatDate } from '../format';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function ScheduleSettings() {
  const schedule = useSchedule();
  const save = useSaveSchedule();
  const [draft, setDraft] = useState<Settings | null>(null);

  useEffect(() => {
    if (schedule.data && draft === null) {
      const { enabled, time, days } = schedule.data;
      setDraft({ enabled, time, days });
    }
  }, [schedule.data, draft]);

  const data = schedule.data;
  const changed =
    data &&
    draft &&
    (draft.enabled !== data.enabled ||
      draft.time !== data.time ||
      draft.days.join() !== data.days.join());

  return (
    <Card withBorder>
      <Stack gap="sm">
        <Title order={4}>Schedule</Title>
        {schedule.isPending && <Loader size="sm" />}
        {schedule.isError && <Alert color="red">{errorMessage(schedule.error)}</Alert>}
        {data && draft && (
          <>
            <Switch
              label="Back up automatically"
              checked={draft.enabled}
              onChange={(event) => setDraft({ ...draft, enabled: event.currentTarget.checked })}
            />
            <Group align="flex-end" gap="md">
              <TextInput
                type="time"
                label="Time"
                description={`Server time (${data.timezone})`}
                value={draft.time}
                disabled={!draft.enabled}
                onChange={(event) => setDraft({ ...draft, time: event.currentTarget.value })}
                w={180}
              />
              <Stack gap={4}>
                <Text size="sm" fw={500}>
                  Days
                </Text>
                <Chip.Group
                  multiple
                  value={draft.days.map(String)}
                  onChange={(values) =>
                    setDraft({ ...draft, days: values.map(Number).sort((a, b) => a - b) })
                  }
                >
                  <Group gap={4}>
                    {DAYS.map((day, index) => (
                      <Chip key={day} value={String(index)} size="xs" disabled={!draft.enabled}>
                        {day}
                      </Chip>
                    ))}
                  </Group>
                </Chip.Group>
              </Stack>
            </Group>
            {draft.enabled && draft.days.length === 0 && (
              <Text size="sm" c="red">
                Choose at least one day.
              </Text>
            )}
            <Group gap="sm">
              <Button
                size="xs"
                disabled={!changed || draft.days.length === 0 || !draft.time}
                loading={save.isPending}
                onClick={() => save.mutate(draft)}
              >
                Save
              </Button>
              <Text size="sm" c="dimmed">
                {data.next_run
                  ? `Next backup: ${formatDate(data.next_run)}`
                  : !data.enabled
                    ? 'Automatic backups are off.'
                    : 'No backup is planned: '}
                {data.enabled && !data.next_run && (
                  <Anchor component={Link} to="/apps" size="sm">
                    add apps to the backup schedule
                  </Anchor>
                )}
              </Text>
            </Group>
            {save.isError && <Alert color="red">{errorMessage(save.error)}</Alert>}
          </>
        )}
      </Stack>
    </Card>
  );
}
