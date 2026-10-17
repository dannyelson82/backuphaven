import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  Group,
  Loader,
  Modal,
  NumberInput,
  PasswordInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { useState } from 'react';
import {
  type AlertSettings as Settings,
  type ChannelInput,
  type ChannelType,
  type ChannelView,
  useAlertSettings,
  useRemoveChannel,
  useSaveChannel,
  useSaveSummary,
  useTestChannel,
} from '../api/alerts';
import { errorMessage } from '../api/client';

const TYPES: { value: ChannelType; label: string }[] = [
  { value: 'ntfy', label: 'ntfy (phone notifications)' },
  { value: 'discord', label: 'Discord' },
  { value: 'homeassistant', label: 'Home Assistant webhook' },
  { value: 'email', label: 'Email' },
  { value: 'unraid', label: 'Unraid notifications' },
];

const LABEL: Record<ChannelType, string> = {
  ntfy: 'ntfy',
  discord: 'Discord',
  homeassistant: 'Home Assistant',
  email: 'Email',
  unraid: 'Unraid',
};

/** What the secret field is called for each type, and whether it's needed. */
const SECRET: Record<ChannelType, { label: string; help: string; required: boolean }> = {
  ntfy: { label: 'Access token', help: 'Only for a protected topic', required: false },
  discord: {
    label: 'Webhook address',
    help: 'Discord channel → Edit → Integrations → Webhooks → Copy URL',
    required: true,
  },
  homeassistant: {
    label: 'Webhook address',
    help: 'An automation with a Webhook trigger, e.g. http://homeassistant:8123/api/webhook/<id>',
    required: true,
  },
  email: { label: 'Password', help: 'For the SMTP login, if it needs one', required: false },
  unraid: {
    label: 'API key',
    help: 'Unraid → Settings → Management Access → API Keys; needs notifications access',
    required: true,
  },
};

function empty(type: ChannelType): ChannelInput {
  const settings: Record<string, string | number> =
    type === 'ntfy'
      ? { server: 'https://ntfy.sh', topic: '' }
      : type === 'email'
        ? { host: '', port: 587, security: 'starttls', username: '', sender: '', to: '' }
        : type === 'unraid'
          ? { url: '' }
          : {};
  return { type, name: LABEL[type], enabled: true, kinds: [], settings, secret: null };
}

function ChannelForm({
  initial,
  editing,
  kinds,
  onClose,
}: {
  initial: ChannelInput;
  editing: ChannelView | null;
  kinds: Record<string, string>;
  onClose: () => void;
}) {
  const save = useSaveChannel();
  const [draft, setDraft] = useState<ChannelInput>(initial);
  const set = (key: string, value: string | number) =>
    setDraft({ ...draft, settings: { ...draft.settings, [key]: value } });
  const field = (key: string, label: string, description?: string) => (
    <TextInput
      label={label}
      description={description}
      value={String(draft.settings[key] ?? '')}
      onChange={(event) => set(key, event.currentTarget.value)}
    />
  );
  const secret = SECRET[draft.type];
  const alertKinds = Object.entries(kinds).filter(([k]) => k !== 'summary');

  return (
    <Modal opened onClose={onClose} title={editing ? `Edit ${editing.name}` : 'Add a channel'}>
      <Stack gap="sm">
        {!editing && (
          <Select
            label="Type"
            data={TYPES}
            value={draft.type}
            allowDeselect={false}
            onChange={(value) =>
              value && setDraft({ ...empty(value as ChannelType), kinds: draft.kinds })
            }
          />
        )}
        <TextInput
          label="Name"
          value={draft.name}
          onChange={(event) => setDraft({ ...draft, name: event.currentTarget.value })}
        />
        {draft.type === 'ntfy' && (
          <>
            {field('server', 'Server', 'https://ntfy.sh, or your own ntfy server')}
            {field('topic', 'Topic', 'Letters, digits, - and _; pick something hard to guess')}
          </>
        )}
        {draft.type === 'email' && (
          <>
            <SimpleGrid cols={2}>
              {field('host', 'SMTP server')}
              <NumberInput
                label="Port"
                value={Number(draft.settings.port ?? 587)}
                onChange={(value) => set('port', Number(value) || 587)}
              />
            </SimpleGrid>
            <Select
              label="Security"
              data={[
                { value: 'starttls', label: 'STARTTLS (port 587)' },
                { value: 'ssl', label: 'SSL/TLS (port 465)' },
                { value: 'none', label: 'None' },
              ]}
              value={String(draft.settings.security ?? 'starttls')}
              allowDeselect={false}
              onChange={(value) => value && set('security', value)}
            />
            {field('username', 'Login name', 'Leave empty if the server needs no login')}
            {field('sender', 'From', 'e.g. backuphaven@example.com')}
            {field('to', 'To', 'One or more addresses, separated by commas')}
          </>
        )}
        {draft.type === 'unraid' && field('url', 'Unraid address', 'http://<your server>/graphql')}
        <PasswordInput
          label={secret.label}
          description={
            editing?.has_secret ? `${secret.help}. Leave empty to keep the saved one.` : secret.help
          }
          value={draft.secret ?? ''}
          onChange={(event) => setDraft({ ...draft, secret: event.currentTarget.value || null })}
        />
        <Stack gap={4}>
          <Text size="sm" fw={500}>
            Send these alerts
          </Text>
          <Checkbox.Group
            value={draft.kinds}
            onChange={(value) => setDraft({ ...draft, kinds: value })}
          >
            <SimpleGrid cols={2} spacing={4}>
              {alertKinds.map(([kind, label]) => (
                <Checkbox key={kind} value={kind} label={label} size="xs" />
              ))}
            </SimpleGrid>
          </Checkbox.Group>
          <Checkbox
            size="xs"
            label="Morning summary"
            checked={draft.kinds.includes('summary')}
            onChange={(event) =>
              setDraft({
                ...draft,
                kinds: event.currentTarget.checked
                  ? [...draft.kinds, 'summary']
                  : draft.kinds.filter((k) => k !== 'summary'),
              })
            }
          />
        </Stack>
        {save.isError && <Alert color="red">{errorMessage(save.error)}</Alert>}
        <Group justify="flex-end">
          <Button variant="default" onClick={onClose}>
            Cancel
          </Button>
          <Button
            loading={save.isPending}
            disabled={!draft.name || (secret.required && !draft.secret && !editing?.has_secret)}
            onClick={() =>
              save.mutate({ id: editing?.id ?? null, body: draft }, { onSuccess: onClose })
            }
          >
            Save
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}

function ChannelRow({ channel, onEdit }: { channel: ChannelView; onEdit: () => void }) {
  const test = useTestChannel();
  const remove = useRemoveChannel();
  return (
    <Stack gap={4}>
      <Group justify="space-between" wrap="nowrap">
        <Group gap="xs">
          <Text fw={500}>{channel.name}</Text>
          <Badge variant="light" size="sm">
            {LABEL[channel.type]}
          </Badge>
          {!channel.enabled && (
            <Badge variant="light" color="gray" size="sm">
              Off
            </Badge>
          )}
        </Group>
        <Group gap={4} wrap="nowrap">
          <Button
            size="xs"
            variant="default"
            loading={test.isPending}
            onClick={() => test.mutate(channel.id)}
          >
            Send test
          </Button>
          <Button size="xs" variant="subtle" onClick={onEdit}>
            Edit
          </Button>
          <Button
            size="xs"
            variant="subtle"
            color="red"
            loading={remove.isPending}
            onClick={() => remove.mutate(channel.id)}
          >
            Remove
          </Button>
        </Group>
      </Group>
      {test.data?.ok && (
        <Text size="xs" c="green.8">
          Test sent. Check that it arrived.
        </Text>
      )}
      {test.data && !test.data.ok && (
        <Text size="xs" c="red">
          Test failed: {test.data.error}
        </Text>
      )}
    </Stack>
  );
}

function Summary({ data }: { data: Settings }) {
  const save = useSaveSummary();
  const [time, setTime] = useState(data.summary_time);
  return (
    <Group align="flex-end" gap="sm">
      <Switch
        label="Morning summary"
        description="How last night's backup went, every day"
        checked={data.summary_enabled}
        onChange={(event) =>
          save.mutate({ summary_enabled: event.currentTarget.checked, summary_time: time })
        }
      />
      <TextInput
        type="time"
        aria-label="Summary time"
        value={time}
        w={120}
        onChange={(event) => setTime(event.currentTarget.value)}
        onBlur={() =>
          time !== data.summary_time &&
          save.mutate({ summary_enabled: data.summary_enabled, summary_time: time })
        }
      />
    </Group>
  );
}

export function AlertSettings() {
  const settings = useAlertSettings();
  const [form, setForm] = useState<{ initial: ChannelInput; editing: ChannelView | null } | null>(
    null,
  );
  const data = settings.data;
  const defaults = data ? Object.keys(data.kinds).filter((k) => k !== 'summary') : [];

  return (
    <Card withBorder>
      <Stack gap="sm">
        <Group justify="space-between">
          <Title order={4}>Alerts</Title>
          {data && (
            <Button
              size="xs"
              onClick={() =>
                setForm({
                  initial: { ...empty('ntfy'), kinds: [...defaults, 'summary'] },
                  editing: null,
                })
              }
            >
              Add a channel
            </Button>
          )}
        </Group>
        {settings.isPending && <Loader size="sm" />}
        {settings.isError && <Alert color="red">{errorMessage(settings.error)}</Alert>}
        {data && (
          <>
            <Text size="sm" c="dimmed">
              BackupHaven tells you when a backup fails, a database is damaged, a backup is missed,
              there&apos;s been no good backup for 36 hours, or the backups disk is nearly full, and
              again when it&apos;s fixed. The same alert isn&apos;t repeated for 12 hours.
            </Text>
            {data.channels.length === 0 && (
              <Text size="sm">No channels yet: alerts only show on the Dashboard.</Text>
            )}
            {data.channels.map((channel) => (
              <ChannelRow
                key={channel.id}
                channel={channel}
                onEdit={() =>
                  setForm({
                    initial: {
                      type: channel.type,
                      name: channel.name,
                      enabled: channel.enabled,
                      kinds: channel.kinds,
                      settings: channel.settings,
                      secret: null,
                    },
                    editing: channel,
                  })
                }
              />
            ))}
            <Summary data={data} />
          </>
        )}
        {form && data && (
          <ChannelForm
            initial={form.initial}
            editing={form.editing}
            kinds={data.kinds}
            onClose={() => setForm(null)}
          />
        )}
      </Stack>
    </Card>
  );
}
