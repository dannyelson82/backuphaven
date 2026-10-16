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
capabilities dropped except `CAP_DAC_READ_SEARCH`, `CAP_DAC_OVERRIDE`,
`CAP_CHOWN`, `CAP_FOWNER`, `CAP_SETUID` and `CAP_SETGID` (the last two only
to start program plugins as an unprivileged user). Its own files in `/config`
and `/backups` are owned by PUID:PGID. No privileged mode.

## Consequences
BackupHaven can back up and restore exactly. A bug in BackupHaven could damage
any app's files, so the file-access layer (scoping, `realpath` checks, write
only during restore) is critical and heavily tested.
