# 0006: Main process runs as root with reduced capabilities

- Status: accepted
- Date: 2026-10-16

## Context
App folders belong to many different users (Plex 99, some apps root, databases
999). BackupHaven must read all of them and, on restore, recreate files with
their original owners and permissions. ReelHaven runs as PUID:PGID, which
can't do either.

## Decision
The main process runs as root inside the container, with all Linux
capabilities dropped except `CAP_DAC_OVERRIDE` (read and write any file),
`CAP_CHOWN`, `CAP_FOWNER` and `CAP_FSETID` (restore owners and permissions
exactly), and `CAP_SETUID` and `CAP_SETGID` (only to start program plugins as an
unprivileged user). The entrypoint removes everything else from the bounding set
and sets `no_new_privs`; CI checks the running process's capabilities.

Amended 2026-10-16 while building phase 0.1: `CAP_DAC_READ_SEARCH` was listed
at first, but Docker doesn't grant it by default and `CAP_DAC_OVERRIDE` already
allows reading any file; `CAP_FSETID` was added so restores can put back
set-group-ID permissions. Its own files in `/config`
and `/backups` are owned by PUID:PGID. No privileged mode.

## Consequences
BackupHaven can back up and restore exactly. A bug in BackupHaven could damage
any app's files, so the file-access layer (scoping, `realpath` checks, write
only during restore) is critical and heavily tested.
