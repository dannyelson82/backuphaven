# Phase 0.2: Every night

Status: **approved by the owner on 2026-10-16** ("bundle everything into
version 0.2 … and proceed"). There is no separate 0.1.0 release: phase 0.1 and
the fixes from the owner's first tests ship in **0.2.0**.

Goal: by the end of 0.2, BackupHaven backs up the apps the owner has added to
the backup schedule every night on its own, keeps a sensible set of old
backups and deletes the rest, never fills the disk, and has a Dashboard,
Backups and Apps pages.

## Already done during 0.1 testing (in 0.2.0)

Plex checks without its search add-on (#15); Choose apps, Cancel, start-up
clean-up, Mac `._` files ignored (#16); per-database timings (#17); Settings →
Developer → Diagnostics files (#18); Plex folder walk 160 s → seconds (#19);
removed apps skipped and listed at the end, ADR-0011 (#20).

## Chunks (one pull request each)

| # | Chunk | Contents |
|---|---|---|
| 1 | **Backup schedule list and Apps page** | ADR-0011: list of apps, empty by default. Apps page: every app found, plugin, folders, last result; **Add to backup schedule** / **Remove**. Run now backs up the list (says to add apps when empty). Navigation bar: Dashboard, Backups, Apps, Settings |
| 2 | **Scheduler** | In-app timer, no cron. Daily at a set time (default 03:00) or on chosen weekdays; server time zone from `TZ`. Settings → Schedule. Dashboard shows the next run. A run missed while the server was off is reported, not run late (default) |
| 3 | **Keep rules** | Pure function with tests (ARCHITECTURE §6.8): 7 nightly, 4 weekly, 6 monthly; never delete the newest good backup or the last good copy of any database; failed ones go after the next good one. Applied after each run. Settings → Keeping backups, including minimum free space (moves from the environment variable to a setting) |
| 4 | **Backups page** | Every backup file: date, result, size, apps, database checks, why it is kept. Delete one by hand (with confirmation; the last good copy of a database is protected). Download and restore come in 0.3 |
| 5 | **Dashboard** | The status page grows into the Dashboard: last and next run, backup schedule summary, storage and free space, database health, anything needing attention at the top |
| 6 | **Release 0.2.0** | Version, release notes, tag, `:latest` image, Unraid template default, owner test |

## Out of scope for 0.2

Restore and download (0.3), alerts (0.4), stop mode (0.5), program plugins and
encryption (0.6), wizard (0.7).

## What the owner will need to do

- After each chunk: **Force Update** on the `:edge` container to try it (optional).
- After chunk 1: add apps to the backup schedule on the Apps page.
- At release: switch the container's Repository from `:edge` to `:latest`
  (steps will be given).
