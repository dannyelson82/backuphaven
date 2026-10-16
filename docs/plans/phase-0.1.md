# Phase 0.1: Foundation and engine

Status: **approved by the owner on 2026-10-16**.

Goal: by the end of 0.1, BackupHaven runs on the owner's Unraid server and
**Run now** produces one verified archive containing Sonarr, Radarr, Prowlarr and
Plex (database and settings), with every database checked, from both
data shares (the array one and the cache-only one). Nightly scheduling,
keep rules and restore come
in 0.2 and 0.3.

## Chunks (one pull request each)

| # | Chunk | Contents |
|---|---|---|
| 1 | **Skeleton and CI** | Backend and frontend skeletons, lint/format/types/tests, image build, container smoke test, GHCR publishing (`:edge` from `main`), Unraid template XML, repo security settings |
| 2 | **Sandbox check** | A startup self-test that reports whether Landlock (and which version) and the needed seccomp calls work inside the container. Logged and shown on the status page. Decides ADR-0004's path before any plugin code depends on it |
| 3 | **Foundation** | Login, first-run admin, sessions, API key, settings store, audit log, secrets encryption: adapted from ReelHaven |
| 4 | **Discovery** | Read Unraid templates from `/boot` (read-only); normalise host paths; match data folders to mounted data shares; flag "data share not added". Pure parsing and matching functions with tests |
| 5 | **Plugins and recipes** | `plugin.yaml` schema (plugin API v1), recipe validation, plugin matching by image, plugin host with app scoping and `realpath` checks, file-access layer (no writes outside restores) |
| 6 | **SQLite snapshot and check** | Online backup API copy, `integrity_check` on the copy; tests with healthy, damaged and actively-written databases; Plex collation handling |
| 7 | **Archive and verifier** | Streamed pax tar + zstd, manifest with SHA-256, manifest copy beside the archive, read-back verification |
| 8 | **Run now and status** | Run engine (one app at a time, failures isolated), Run now, run progress, basic status page (last run, apps, database health), bundled recipes: generic, Sonarr, Radarr, Prowlarr, Plex, BackupHaven; guide pages |
| 9 | **Release 0.1.0** | Version, release notes, owner test |

## Out of scope for 0.1

Schedule and keep rules (0.2), restore and download (0.3), alerts (0.4), stop
mode and socket proxy (0.5), program plugins and encryption (0.6), wizard and
flash backup (0.7).

## What the owner will need to do

- **During chunk 6:** copy one Plex database into the projects folder once
  (Plex stopped), so the database check can be tested against the real format.
  Removed afterwards.
- **After chunk 2 and at release:** install or update the BackupHaven container
  from its template and add both data shares; exact steps will be given.
- **At release:** press Run now and check the status page.

## Decisions (2026-10-16)

The owner approved ARCHITECTURE.md, SECURITY.md and ADR-0001 to ADR-0009; a
public repository; web port 7272; and a PlanHaven project for notes and bugs.
