---
title: Backing up
order: 30
summary: Run a backup, read the Dashboard, and know what the results mean.
---

# Backing up

The page you see after logging in is the **Dashboard**. Anything that needs
your attention is shown at the top.

## Run now

**Run now** backs up the apps in the [backup schedule](apps.md) straight away.
While the schedule is empty, Run now can't be pressed and the Dashboard says
how to add apps. While a backup runs, the page shows which
app it's on, how many apps are done, and how much it has saved so far. You can
close the page; the backup carries on.

Each backup is **one file** in your backups folder, named after its date and
time, for example `backuphaven-2026-10-17T030000.tar.zst`. A copy of its
contents list (`….manifest.json`) sits next to it.

### Back up only some apps

**Choose apps…** (next to Run now) lists every app BackupHaven found. Tick the
ones you want and press **Back up N apps**. Only those apps go into the backup
file, whether they are in the backup schedule or not, and the schedule doesn't
change. This is handy for a quick test, or for one app before you update it.

### Cancel a backup

While a backup runs, a **Cancel** button sits next to the progress bar. It
stops after the file it is reading, and the half-written backup file is
deleted. Your apps are not affected. Cancel isn't offered in the last step,
while the backup file is being checked, which is quick.

If BackupHaven is restarted during a backup, that backup is marked **Failed**
("interrupted by a restart"), and the half-written file is deleted the next
time BackupHaven starts.

Coming in version 0.2: backups every night, and old backups cleared out
automatically.

## What a backup does

For every app BackupHaven finds:

1. It copies the files the app's [plugin](plugins.md) asks for.
2. It copies each **database** while the app keeps running, then **checks the
   copy for damage**.
3. It stores the app's container settings (its Unraid template), so the
   container itself can be recreated.

When every app is done, BackupHaven **reads the whole backup file back** to
make sure it can be restored, and only then gives it its final name. A file
named `backuphaven-….tar.zst` in the backups folder has always passed that
check.

## The results

| Result | Meaning |
|---|---|
| **OK** | Everything was backed up and every database is healthy. |
| **Warnings** | The backup is fine, but something is worth knowing, for example a file that changed while it was being copied, or an app on a share BackupHaven can't see. |
| **Failed** | Something couldn't be backed up, or a database is damaged. The apps that worked are still in the backup file. |
| **Cancelled** | Someone pressed Cancel. No backup file was kept. |

Below the results, **Skipped** lists apps that weren't backed up on purpose,
with the reason. **not installed** means none of the app's folders exist any
more: the container was removed, but Unraid keeps its template. A skipped app
doesn't make the backup **Warnings** or **Failed**. If the app should still be
there, check its container and its folders.

## Database health

The **Database health** table lists every database from the last backup:

- **Healthy**: the copy was checked and is fine.
- **quick check** next to it: the database uses features BackupHaven can't fully
  check (Plex does), so its structure was checked instead. Plex's search index
  can't be opened outside Plex, so it is left out of the check; everything else
  is checked, and the search index is still in the backup.
- **Damaged**: the copy has errors, which means the app's own database is
  damaged. Look at the app soon, and keep older backups: they may hold a healthy
  copy.
- **Not checked**: the check took too long. The copy is still in the backup.

## Shares BackupHaven can't see

If an app in the backup schedule keeps its data on a share that BackupHaven
hasn't been given, the
Dashboard says which share to add. See [Installing](install.md#data-shares).
