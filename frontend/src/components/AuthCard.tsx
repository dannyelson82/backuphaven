import { Center, Image, Paper, Stack, Text, Title } from '@mantine/core';
import type { ReactNode } from 'react';

/** Centred card with the logo, used by the setup and login screens. */
export function AuthCard({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <Center mih="100vh" p="md">
      <Paper withBorder shadow="sm" p="xl" w="100%" maw={420}>
        <Stack gap="lg">
          <Stack align="center" gap={4}>
            <Image src="./favicon.svg" w={64} h={64} alt="" />
            <Title order={2}>{title}</Title>
            {subtitle && (
              <Text c="dimmed" ta="center" size="sm">
                {subtitle}
              </Text>
            )}
          </Stack>
          {children}
        </Stack>
      </Paper>
    </Center>
  );
}
