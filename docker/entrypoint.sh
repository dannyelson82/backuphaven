#!/bin/sh
# Container entrypoint (ARCHITECTURE.md §5.3, ADR-0006). BackupHaven stays root
# inside the container so it can read every app's files and restore their
# owners, but keeps only the capabilities that needs: everything else is removed
# from the bounding set before the app starts, and it can never gain more.
set -eu

PUID="${PUID:-99}"
PGID="${PGID:-100}"
UMASK="${UMASK:-022}"

case "$PUID$PGID" in
  *[!0-9]*) echo "PUID and PGID must be numbers" >&2; exit 1 ;;
esac
case "$UMASK" in
  *[!0-7]*) echo "UMASK must be an octal number such as 022" >&2; exit 1 ;;
esac

if [ "$(id -u)" != "0" ]; then
  echo "BackupHaven must start as root inside its container (ADR-0006)." >&2
  exit 1
fi

# BackupHaven's own folders belong to PUID:PGID. /config is fixed recursively
# only when the owner changed; /backups only at the top (it can be large).
if [ "$(stat -c %u /config)" != "$PUID" ]; then
  chown -R "$PUID:$PGID" /config
fi
chown "$PUID:$PGID" /backups

umask "$UMASK"
exec setpriv \
  --bounding-set=-all,+chown,+dac_override,+fowner,+fsetid,+setuid,+setgid \
  --inh-caps=-all --no-new-privs "$@"
