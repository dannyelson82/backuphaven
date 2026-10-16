# 0009: Several data shares, each mounted at its host path

- Status: proposed
- Date: 2026-10-16

## Context
Containers don't all keep their data in one `appdata` share. A server often
has `appdata` (on the array) plus a cache-only share for speed (here: `fast`), and
some have more. Unraid can also name the same folder in several ways
(`/mnt/user/<share>`, `/mnt/cache/<share>`, `/mnt/diskN/<share>`, other pools).

## Decision
- BackupHaven backs up any number of **data shares**. Each is mounted at the
  **same path inside the container as on the host** (e.g. `/mnt/user/fast`
  to `/mnt/user/fast`), so template paths need no translation.
- Discovery normalises every host path to `/mnt/user/<share>/...` before
  matching it to a mounted share.
- An app whose data is on a share that isn't mounted is flagged with the exact
  path to add. It is never silently skipped.
- One app may have data folders on several shares; all are backed up, and the
  archive keeps them apart (`apps/<app>/files/<folder>/`).
- Restores move the current folder aside **on the same share** (a rename, not a
  copy) and extract there.

## Consequences
Works with any share layout, at the cost of one extra path mapping per data
share in BackupHaven's template, explained in the guide and the wizard. Media
shares are never mounted, so BackupHaven can't read or write them even by
mistake.
