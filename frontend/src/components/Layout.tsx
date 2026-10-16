import { Button, Container, Group, Image, Stack, Text, Title } from '@mantine/core';
import type { ReactNode } from 'react';
import { NavLink } from 'react-router';
import { type AuthState, useLogout } from '../api/auth';

const PAGES = [
  { to: '/', label: 'Dashboard' },
  { to: '/apps', label: 'Apps' },
  { to: '/settings', label: 'Settings' },
];

/** Header with the navigation, around every page after login. */
export function Layout({ auth, children }: { auth: AuthState; children: ReactNode }) {
  const logout = useLogout();
  return (
    <Container size="md" py="md">
      <Stack gap="md">
        <Group justify="space-between">
          <Group gap="sm">
            <Image src="./favicon.svg" w={36} h={36} alt="" />
            <Title order={2}>BackupHaven</Title>
          </Group>
          <Group gap="xs">
            <Text size="sm" c="dimmed">
              {auth.method === 'bypass' ? 'Local network access' : auth.username}
            </Text>
            {auth.method === 'session' && (
              <Button variant="default" size="xs" onClick={() => logout.mutate()}>
                Log out
              </Button>
            )}
          </Group>
        </Group>
        <Group gap={4} component="nav" aria-label="Pages">
          {PAGES.map((page) => (
            <NavLink key={page.to} to={page.to} end style={{ textDecoration: 'none' }}>
              {({ isActive }) => (
                <Button component="span" size="xs" variant={isActive ? 'light' : 'subtle'}>
                  {page.label}
                </Button>
              )}
            </NavLink>
          ))}
        </Group>
        {children}
      </Stack>
    </Container>
  );
}
