import { Alert, Center, Loader } from '@mantine/core';
import { Route, Routes } from 'react-router';
import { useAuthState } from '../api/auth';
import { AppsPage } from '../pages/AppsPage';
import { BackupPage } from '../pages/BackupPage';
import { BackupsPage } from '../pages/BackupsPage';
import { errorMessage } from '../api/client';
import { SettingsPage } from '../pages/SettingsPage';
import { StatusPage } from '../pages/StatusPage';
import { LoginPage } from '../pages/LoginPage';
import { SetupPage } from '../pages/SetupPage';
import { Layout } from './Layout';

/** Shows setup, login or the app depending on the server's auth state. */
export function AuthGate() {
  const auth = useAuthState();

  if (auth.isPending) {
    return (
      <Center mih="100vh">
        <Loader />
      </Center>
    );
  }
  if (auth.isError) {
    return (
      <Center mih="100vh" p="md">
        <Alert color="red" title="BackupHaven is not responding">
          {errorMessage(auth.error)}
        </Alert>
      </Center>
    );
  }
  if (auth.data.setup_required) return <SetupPage />;
  if (!auth.data.authenticated) return <LoginPage />;

  return (
    <Layout auth={auth.data}>
      <Routes>
        <Route path="/backups/:runId" element={<BackupPage />} />
        <Route path="/backups" element={<BackupsPage />} />
        <Route path="/apps" element={<AppsPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<StatusPage />} />
      </Routes>
    </Layout>
  );
}
