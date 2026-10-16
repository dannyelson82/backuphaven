# 0013: Plugins only: each app's plugin makes a complete, restorable copy

- Status: accepted (owner, 2026-10-16)
- Date: 2026-10-16
- Amends: ADR-0003, ARCHITECTURE.md §6.1–6.3, §9.2

## Context
Apps without a plugin of their own were backed up by the **generic plugin**:
every file in every data folder, SQLite databases found by their header. Testing
on the owner's server showed the limits of guessing: PlanHaven's PostgreSQL
files were copied while running (they may not restore) and its attachments, on a
share of their own, were missed; Audiobookshelf's metadata folder, inside its
config folder, was stored twice.

The owner decided: "do away with the whole general back up of all apps and just
do each app as its own plugin, where the app's plugin is responsible for backing
up each app as a complete restorable image", and: "plugins only, but have a tool
that generates a plugin development file that can be fed to an AI coder to
quickly develop a plugin for the app and register it with the git repo".

## Decision
- **No generic plugin.** An app is backed up only by a plugin made for it. Apps
  without one show **No plugin yet** and can't be added to the backup schedule.
- **A plugin's promise:** restoring its backup brings the app back in working
  order. It covers every data folder the app needs (including whole shares, via
  `share_folders`), copies databases consistently (SQLite snapshots; for database
  servers, the app's own dumps until stop mode), and records what must exist
  (`expect`), so a backup that would not restore fails instead.
- **Completeness check on every backup:** a data folder of the app that the
  plugin doesn't cover (and that isn't inside one it covers), or a database
  server folder among the copied files, is reported on the app's result.
- **Proven restorable:** every bundled plugin ships a small sample of its app's
  files (a fixture). CI backs the sample up with the plugin, restores it into an
  empty folder and checks the result. A plugin without a passing fixture can't
  be merged.
- **Plugin development file:** **Make a plugin** on the Apps page downloads a
  Markdown brief for an AI coding assistant: the app's image and folder
  mappings, a map of its data folders (names, sizes, kinds, never contents), the
  plugin format, the promise above, the fixture rules, and the steps to open a
  pull request to the BackupHaven repository. The brief tells the assistant to
  keep the owner's server details (share names, paths, personal file names) out
  of the repository. Merged plugins are bundled in the next release.

## Consequences
- Backups are only as wide as the plugin catalogue: a new app is unprotected
  until it has a plugin. The Apps page makes this visible, and the development
  file makes adding one quick.
- Plugins become reviewed code in the repository, with tests; the quality of a
  backup no longer depends on guessing.
- Existing schedules: apps that used the generic plugin get their own plugins in
  the same release (Audiobookshelf, Cantinarr, Chaptarr for the owner).
- Third-party plugin installation (0.6) is unchanged: it adds a catalogue; the
  same promise and fixture rules apply.
