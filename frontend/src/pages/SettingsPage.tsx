import { Alert, Card, Code, Loader, Stack, Switch, Table, Text, Title } from '@mantine/core';
import { errorMessage } from '../api/client';
import { ScheduleSettings } from '../components/ScheduleSettings';
import { useDeveloperSettings, useSetDiagnostics } from '../api/developer';
import { formatBytes, timeAgo } from '../format';

function Developer() {
  const developer = useDeveloperSettings();
  const setDiagnostics = useSetDiagnostics();
  return (
    <Card withBorder>
      <Stack gap="sm">
        <Title order={4}>Developer</Title>
        {developer.isPending && <Loader size="sm" />}
        {developer.isError && <Alert color="red">{errorMessage(developer.error)}</Alert>}
        {developer.data && (
          <>
            <Switch
              label="Diagnostics files"
              description="Writes a detailed log and a summary of every backup, for tracking down problems. Small files; old ones are removed automatically. Leave it off unless you're investigating something."
              checked={developer.data.diagnostics}
              disabled={setDiagnostics.isPending}
              onChange={(event) => setDiagnostics.mutate(event.currentTarget.checked)}
            />
            {setDiagnostics.isError && (
              <Alert color="red">{errorMessage(setDiagnostics.error)}</Alert>
            )}
            <Text size="sm">
              Folder: <Code>{developer.data.folder}</Code>
            </Text>
            {developer.data.files.length > 0 && (
              <Table.ScrollContainer minWidth={360}>
                <Table verticalSpacing={4}>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>File</Table.Th>
                      <Table.Th>Size</Table.Th>
                      <Table.Th>Changed</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {developer.data.files.map((file) => (
                      <Table.Tr key={file.name}>
                        <Table.Td>
                          <Code>{file.name}</Code>
                        </Table.Td>
                        <Table.Td>{formatBytes(file.size)}</Table.Td>
                        <Table.Td>{timeAgo(file.modified)}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </Table.ScrollContainer>
            )}
          </>
        )}
      </Stack>
    </Card>
  );
}

export function SettingsPage() {
  return (
    <Stack gap="md">
      <Title order={3}>Settings</Title>
      <ScheduleSettings />
      <Developer />
    </Stack>
  );
}
