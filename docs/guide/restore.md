---
title: Restoring an app
order: 37
summary: Put an app's files back as they were in a backup, and undo it if needed.
---

# Restoring an app

A restore puts one app's files back exactly as they were in a backup: settings,
databases, owners and permissions. Your current files aren't deleted: they're
moved aside first, so you can **undo** the restore for 7 days.

## Starting a restore

Either:

- **Backups** → **Open** a backup → open the app → **Restore *app*…**; or
- **Apps** → **Restore…** next to the app → choose a backup (newest first).

Then follow the three steps on the page:

1. **Stop the container.** In Unraid, go to **Docker**, click the app and
   choose **Stop**. Press **Check it's stopped**: BackupHaven watches the app's
   files for a few seconds. If they're still changing, the app is still running,
   and it says which files changed.
2. **Restore.** It lists the folders that will be replaced. Tick the box to
   confirm, then press **Restore *app***. Big apps take a little while; the page
   shows what it's doing.
3. **Start it again.** In Unraid, start the container.

BackupHaven won't restore while a backup is running, and backups wait for a
restore to finish.

## What happens to your current files

The app's current folder is moved (not copied) to a `.backuphaven-restore`
folder on the same share, for example
`/mnt/user/appdata/.backuphaven-restore/sonarr-2026-10-17T101500/`. The backup is
unpacked into a fresh folder in its place.

Things the backup leaves out on purpose (caches, Plex's posters and artwork,
logs) are then moved back from the set-aside folder, so Plex doesn't have to
download all its artwork again. Old database side files (`-wal`, `-shm`) are
not moved back: they belong to the old database and could damage the restored
one.

If anything goes wrong during the restore, for example the backup turns out to
be damaged, BackupHaven removes what it unpacked and puts your files back as
they were. Nothing changes.

## Undo

If the app is worse after a restore, stop its container again, then press
**Undo**: on the restore page right after restoring, or under **Recent
restores** on the Backups page. Your files go back to how they were just before
the restore. Then start the container.

Undo is possible for 7 days. After that the set-aside folder is deleted to free
the space.

## What isn't restored

- The container's settings in Unraid (ports, paths): the flash drive is
  read-only to BackupHaven. The backup holds them as `template.xml`, which you
  can download from the backup.
- Files the app's plugin doesn't back up (see [Plugins](plugins.md)).
