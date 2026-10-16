# Phase 0.3: Restore

Status: **approved by the owner on 2026-10-16** ("continue to next phase");
the two behaviours marked *recommended* below are open to the owner's veto
before chunk 3. Released as **0.0.3.0** (ADR-0012).

Goal: from the Backups or Apps page, the owner can see what a backup holds,
download a whole backup, one app or one file, and restore an app to how it was
in any backup, then undo that restore if it made things worse.

## How a restore works (ARCHITECTURE.md §6.9, ADR-0007)

1. Choose an app and a backup (newest good one preselected).
2. BackupHaven asks the owner to **stop the container in Unraid** (no Docker
   access until stop mode in 0.5), then checks the app's files aren't changing
   for a few seconds. A database still open by a running app is refused.
3. The app's current folder is **moved aside** to
   `<share>/.backuphaven-restore/<app>-<time>/`: a rename on the same share, no
   copying, nothing deleted.
4. The backup is unpacked into the app's folder: files, links, owners,
   permissions and times as they were; databases from their checked copies. Every
   file's SHA-256 is checked against the backup's contents list while it's
   written.
5. *Recommended:* what the backup deliberately left out (Plex's posters and
   caches, logs) is **moved back** from the set-aside folder, so Plex doesn't
   have to download tens of gigabytes of artwork again.
6. The owner starts the container again. **Undo** swaps the folders back, for
   7 days; after that the set-aside folder is deleted. *Recommended:* 7 days,
   changeable later in Settings.

Writes into data shares happen only inside a restore the owner started, only in
that app's folders, through one file-access layer; a test checks that a
backup run opens nothing there for writing.

## Chunks (one pull request each)

| # | Chunk | Contents |
|---|---|---|
| 1 | **Safe unpacking** | Archive reader for one app or one file: refuses absolute paths, `..`, devices, FIFOs, hard links and anything escaping its folder; never follows links while writing; restores owner/mode/time; checks SHA-256 against the manifest. Many hostile-archive tests |
| 2 | **Browse and download** | Open a backup on the Backups page: apps, folders, databases and files from its contents list. Download the whole backup, one app (as its own `.tar.zst`) or one file |
| 3 | **Restore and undo** | Restore engine with the steps above, a `restores` table, undo, expiry of set-aside folders, the write guard and its tests |
| 4 | **Restore screens** | **Restore…** on the Backups and Apps pages: step-by-step (stop the container, checks, restore, start it), progress, result, **Undo**; restore history |
| 5 | **Release 0.0.3.0** | Version, notes, tag, owner test on a real app (Sonarr suggested) |

## Out of scope

Stopping/starting containers for the owner (0.5), restoring the Unraid template
(the flash drive is read-only to BackupHaven; the template can be downloaded),
restoring to a different app or path, alerts (0.4).

## What the owner will need to do

- After chunk 4: try a restore and an undo on a small app such as Sonarr
  (exact steps will be given).
