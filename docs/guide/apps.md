---
title: Apps and the backup schedule
order: 25
summary: Choose which apps are backed up by adding them to the backup schedule.
---

# Apps and the backup schedule

BackupHaven backs up only the apps you add to the **backup schedule**. The
schedule starts **empty**, so nothing is backed up until you choose.

## The Apps page

**Apps** (in the menu at the top) lists every container BackupHaven found in
Unraid's templates. For each app it shows:

- the plugin it uses and the folders it would back up;
- its last backup: when, how big, and the result;
- **In the backup schedule** when it's in the schedule.

At the top, **In the backup schedule** shows only the apps in the schedule and
**All apps** shows everything. Use **Search** to find an app by name.

## Adding and removing apps

- Press **Add to backup schedule** next to an app. From then on, **Run now** and
  the nightly backup include it.
- Press **Remove** to take it out of the schedule. Its old backups are kept.

New containers don't join the schedule by themselves: add them when you
install them.

## Apps marked "Not installed"

Unraid keeps the template of every container you have ever installed, so the
list can include apps you removed long ago. When none of an app's folders exist
any more, it's marked **Not installed** and listed last. Don't add these.

If an app in the schedule is removed later, backups **skip** it and list it
under **Skipped** at the end of the results. If Unraid no longer has a template
with that name at all, the Apps page shows **… is no longer found** with a
**Remove** button.

## "Share not added"

The app keeps data on a share BackupHaven can't see. Add that share to
BackupHaven's container (see [Installing](install.md#data-shares)), with the same path on
both sides.
