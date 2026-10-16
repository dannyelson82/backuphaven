# 0011: Apps are added to the backup schedule (empty by default); apps whose folder is gone are skipped

- Status: accepted (owner, 2026-10-16)
- Date: 2026-10-16
- Amends: ARCHITECTURE.md §3, §6.1, §6.5, §7, §8, §9.2

## Context
The design backed up every app found in Unraid's templates, with an on/off switch
per app and a "new apps" setting (back up with the generic plugin, or ask).

Testing on the owner's server found 87 templates for 36 real containers: Unraid
keeps the template of every container ever installed. Some of those apps' folders
are gone, which made a full run report them as failed; others still have
folders and would be backed up as if they were in use. BackupHaven can't tell
which containers exist until the socket proxy (0.5) is set up, and that stays
optional.

The owner chose: "instead of persistent on/off switches let's go with an add to
backup schedule, with it being empty by default", and for apps whose folder is
gone: "skip it but report the problem at the end".

## Decision
- The **backup schedule** holds a list of apps, **empty by default**. The owner
  adds apps on the Apps page (**Add to backup schedule**) and removes them there.
  Scheduled runs (0.2) and **Run now** back up exactly that list. With an empty
  list, Run now doesn't start and says to add apps first.
- **Choose apps…** still backs up any apps once, without changing the schedule.
- An app whose data folders don't exist (on a mounted share) is **skipped**: it
  isn't a failure or a warning, and skipped apps are listed together at the end
  of the run's results with the reason. An app on a share BackupHaven can't see is
  still flagged "data share not added", as before.
- This replaces the per-app on/off switch and the "new apps" setting. New apps
  appear on the Apps page, not in the schedule; once alerts exist (0.4), a new app
  not in the schedule can be reported.
- The list is stored in the `settings` table (key `schedule`) for now; it moves to
  the `apps` table when per-app settings arrive.

## Consequences
- Nothing is backed up until the owner chooses apps: no surprise multi-gigabyte
  first run, and stale templates never cause failures.
- A new container isn't protected until it is added, so the Apps page and (later)
  alerts must make apps that aren't in the schedule easy to see.
- The setup wizard (0.7) gets a "choose apps" step that fills the schedule.
