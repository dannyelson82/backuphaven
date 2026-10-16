import { Alert, Button, Card, Group, Loader, NumberInput, Stack, Text, Title } from '@mantine/core';
import { useEffect, useState } from 'react';
import { errorMessage } from '../api/client';
import { type KeepingSettings as Settings, useKeeping, useSaveKeeping } from '../api/keeping';

const FIELDS: { key: keyof Settings; label: string; max: number; help: string }[] = [
  { key: 'nightly', label: 'Nightly', max: 90, help: 'Days with a backup kept' },
  { key: 'weekly', label: 'Weekly', max: 104, help: 'Weeks, one backup each' },
  { key: 'monthly', label: 'Monthly', max: 120, help: 'Months, one backup each' },
];

export function KeepingSettings() {
  const keeping = useKeeping();
  const save = useSaveKeeping();
  // Kept as typed (number or text) while editing; numbers when saved.
  type Draft = Record<keyof Settings, number | string>;
  const [draft, setDraft] = useState<Draft | null>(null);

  useEffect(() => {
    if (keeping.data && draft === null) setDraft(keeping.data);
  }, [keeping.data, draft]);

  const data = keeping.data;
  const toSettings = (d: Draft): Settings => ({
    nightly: Number(d.nightly) || 0,
    weekly: Number(d.weekly) || 0,
    monthly: Number(d.monthly) || 0,
    min_free_gb: Number(d.min_free_gb) || 0,
  });
  const changed =
    data &&
    draft &&
    (Object.keys(data) as (keyof Settings)[]).some((k) => data[k] !== toSettings(draft)[k]);

  return (
    <Card withBorder>
      <Stack gap="sm">
        <Title order={4}>Keeping backups</Title>
        {keeping.isPending && <Loader size="sm" />}
        {keeping.isError && <Alert color="red">{errorMessage(keeping.error)}</Alert>}
        {draft && (
          <>
            <Text size="sm" c="dimmed">
              After each backup, older ones are deleted except these. The newest good backup and the
              last healthy copy of every database are always kept.
            </Text>
            <Group gap="md" align="flex-start">
              {FIELDS.map((field) => (
                <NumberInput
                  key={field.key}
                  label={field.label}
                  description={field.help}
                  min={0}
                  max={field.max}
                  allowDecimal={false}
                  w={150}
                  value={draft[field.key]}
                  onChange={(value) => setDraft({ ...draft, [field.key]: value })}
                />
              ))}
              <NumberInput
                label="Minimum free space"
                description="GB; backups stop below this"
                min={0}
                max={100000}
                w={170}
                value={draft.min_free_gb}
                onChange={(value) => setDraft({ ...draft, min_free_gb: value })}
              />
            </Group>
            <Group gap="sm">
              <Button
                size="xs"
                disabled={!changed}
                loading={save.isPending}
                onClick={() => save.mutate(toSettings(draft))}
              >
                Save
              </Button>
              {save.isSuccess && !changed && (
                <Text size="sm" c="dimmed">
                  Saved. Used after the next backup.
                </Text>
              )}
            </Group>
            {save.isError && <Alert color="red">{errorMessage(save.error)}</Alert>}
          </>
        )}
      </Stack>
    </Card>
  );
}
