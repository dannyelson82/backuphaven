# 0005: Stop mode through a limited Docker socket proxy

- Status: proposed
- Date: 2026-10-16

## Context
The owner chose "you decide per app": snapshot mode by default, and an optional
per-app "stop during backup". Stopping containers needs Docker. The Docker
socket gives full control of the host (effectively root), which BackupHaven
must never have.

## Decision
- Stop mode, one-click restore and container status use an optional separate
  container, **`linuxserver/socket-proxy`**, configured to allow only container
  listing and start/stop (`CONTAINERS=1`, `ALLOW_START=1`, `ALLOW_STOP=1`,
  everything else 0). BackupHaven reaches it over a private Docker network.
- Without the proxy, everything else works: snapshot mode for all apps, and a
  restore asks the owner to stop and start the container in Unraid.
- BackupHaven never stops itself or the proxy; a plugin may only stop its own
  app.

## Consequences
One extra container for owners who want stop mode, with step-by-step setup in
the guide. Even if BackupHaven were compromised, the most it could do through
the proxy is stop or start containers.
