import { Alert, Card, Center, Image, MantineProvider, Stack, Text, Title } from '@mantine/core';
import { useEffect, useState } from 'react';
import { theme } from './theme';

interface Version {
  name: string;
  version: string;
}

/** Placeholder until the status page exists (phase 0.1, chunk 8). */
export function App() {
  const [version, setVersion] = useState<Version | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    fetch('./api/v1/version')
      .then((response) => {
        if (!response.ok) throw new Error(String(response.status));
        return response.json() as Promise<Version>;
      })
      .then(setVersion)
      .catch(() => setFailed(true));
  }, []);

  return (
    <MantineProvider theme={theme} defaultColorScheme="auto">
      <Center mih="100vh" p="md">
        <Card withBorder shadow="sm" padding="xl" maw={440} w="100%">
          <Stack align="center" gap="sm">
            <Image src="./favicon.svg" w={72} h={72} alt="" />
            <Title order={2}>BackupHaven</Title>
            {version && <Text c="dimmed">Version {version.version}</Text>}
            {failed && (
              <Alert color="red" w="100%">
                Can&apos;t reach the BackupHaven server.
              </Alert>
            )}
            <Text ta="center">
              This is an early test build. It doesn&apos;t back anything up yet; backups arrive
              later in version 0.1.
            </Text>
          </Stack>
        </Card>
      </Center>
    </MantineProvider>
  );
}
