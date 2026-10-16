---
title: The Backups page
order: 35
summary: Every backup file, why it's kept, what's inside, downloading and deleting.
---

# The Backups page

**Backups** (in the menu at the top) lists every backup file in the backups
folder, newest first, with the total size at the top. For each backup:

- the date and result (**OK**, **Warnings**, **Failed**), and **Chosen apps**
  for backups made with **Choose apps…**, with the apps it holds;
- its size, how many apps and how many healthy databases it holds, and
  **checked after writing** once the file has been read back and verified;
- **Kept:** why the [keep settings](settings.md#keeping-backups) keep it (nightly,
  weekly, monthly, newest good backup, the last healthy copy of an app's
  database, chosen apps). **Will be deleted after the next backup** means no
  rule keeps it any more;
- **File missing** if the file was deleted or moved outside BackupHaven.

## Deleting a backup by hand

Press **Delete**, then **Delete backup** to confirm. The file and its contents
list are removed from the backups folder; this can't be undone.

**Delete** can't be pressed for the newest good backup, or for a backup that
holds the last healthy copy of some app's database. That copy might be all
that's left if the database is damaged later. These go once a newer backup takes
their place.

## Looking inside a backup

Press **Open** next to a backup. You'll see every app in it, with its result,
how many files and databases it holds, and its size. Apps that were skipped
can't be opened, because nothing of theirs was stored.

Open an app to list its files:

- `files/<folder>/…`: the app's files, in the folder it uses inside its
  container (usually `config`);
- `databases/<folder>/…`: the checked copies of its databases;
- `template.xml`: the app's Unraid container settings.

With many files, **Find a file** narrows the list down.

## Downloading

- **Download backup**: the whole backup file (`.tar.zst`).
- **Download *app***: just that app, as its own `.tar.zst`.
- Click a file's name to download just that file.

Downloads are made while you wait, without using space on the server. A
`.tar.zst` file opens with 7-Zip (version 23 or newer) on Windows, or with
`tar --zstd -xf <file>` on Linux and macOS.

To put an app back as it was in a backup, see [Restoring an app](restore.md).
Recent restores, with **Undo**, are listed at the top of the Backups page.
