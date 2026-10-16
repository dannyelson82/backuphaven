---
title: The Backups page
order: 35
summary: Every backup file, why it's kept, and deleting one by hand.
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

Restoring and downloading from this page come in a later version.
