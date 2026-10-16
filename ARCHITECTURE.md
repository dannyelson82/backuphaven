# BackupHaven Architecture

Status: **approved by the owner on 2026-10-16**. Decisions are recorded in
`docs/adr/`; anything still open is listed in §14.

## 1. Purpose

BackupHaven is a simple, automated backup app for Unraid. Every night it backs
up the Docker containers' settings and data into **one compressed file**, checks
that every database in it is healthy, keeps a sensible history of backups, and
makes restoring one app easy.

It is built around **plugins**: everything that is specific to one app (Sonarr,
Plex, MariaDB...) or to one place to store backups (a local share, a cloud
provider) lives in a plugin, so other people can add support for new apps and
providers without changing BackupHaven itself.

Goals, in order:

1. **Backups you can trust.** A backup is only "good" once it has been written,
   read back and checked. Database corruption is noticed the night it happens.
2. **Nothing goes offline unless you choose it.** SQLite databases are copied
   live; stopping a container is a per-app choice.
3. **Easy for a non-expert.** Sensible defaults, a setup wizard, plain words,
   and a restore that can be undone.
4. **Safe with other people's plugins.** A plugin can only do what its owner
   approved, whatever its code says.

### 1.1 Non-goals (v1)

- Backing up media libraries (films, TV): too large, and Sonarr/Radarr can
  download them again. BackupHaven protects the *apps*, not the media.
- Backing up Docker images: they are downloaded again from their registry.
  The container's Unraid template (how to recreate it) is backed up instead.
- Backing up VMs, or whole disks.
- Deduplicating or incremental backups (each night is a complete archive).
  Can be revisited if nightly archives become too large.
- Running outside Unraid. It should work on any Docker host, but only Unraid
  is tested and documented.

## 2. Glossary

| Term | Meaning |
|---|---|
| **App** | A Docker container BackupHaven backs up, e.g. Sonarr. |
| **Run** | One backup run (nightly or "Run now"), producing one archive. |
| **Archive** | The single compressed file a run produces (`.tar.zst`). |
| **Manifest** | The list inside each archive: every file, its checksum, every check's result. |
| **Plugin** | Something that teaches BackupHaven about one app, destination or alert channel. |
| **Recipe** | A plugin written as a short settings file (YAML), using built-in actions only. |
| **Program plugin** | A plugin that is a program, run locked in (sandboxed) with approved permissions. |
| **Action** | A built-in, tested operation a plugin can ask for (snapshot a SQLite database, copy files, stop a container...). |
| **Permission** | Something a program plugin is allowed to do, approved by the owner at install time. |
| **Destination** | Where archives are stored: a local folder by default, or a destination plugin. |
| **Snapshot mode** | Back up while the app runs: live SQLite copies plus file copies. |
| **Stop mode** | Stop the container, copy everything, start it again. |
| **Last good copy** | The newest archive in which a given database passed its check. Never deleted by the keep rules. |

## 3. System overview

```
                    ┌────────────────────── BackupHaven container ──────────────────────┐
 Browser ──HTTPS──▶ │ Web UI (React) ─▶ API (FastAPI)                                   │
                    │                     │                                             │
                    │        Scheduler ─▶ Run engine ──▶ Plugin host ──▶ Recipe plugins │
                    │                     │   │                 └──▶ Program plugins    │
                    │                     │   │                        (sandboxed,      │
                    │                     │   │                         own processes)  │
                    │                     │   └─▶ Archive writer ─▶ Verifier ─▶ Keep    │
                    │                     │                                    rules    │
                    │                  SQLite (/config)                                 │
                    └────┬──────────────┬───────────────┬──────────────┬────────────────┘
                         │              │               │              │
        data shares (read/write)    /boot (read-only) /backups   socket-proxy (optional:
        e.g. appdata, fast      Unraid templates  archives   list/stop/start only)
```

A run, step by step:

1. **Discover** apps from the Unraid templates (`/boot/config/plugins/dockerMan/templates-user/*.xml`):
   container name, image, appdata folders. Each app is matched to a plugin by
   image name; apps with no plugin get the **generic plugin**.
2. **For each app in the backup schedule** (empty by default; ADR-0011), in the configured order, the plugin's backup steps
   run through the plugin host:
   - snapshot mode: live-copy each SQLite database, then copy the other files;
   - stop mode: stop the container (through the proxy), copy everything, start it.
3. **Check** every database copy (`PRAGMA integrity_check`, on the *copy*, never
   on the live file), plus any checks the plugin defines.
4. **Write** everything as one streamed `tar` archive compressed with zstd. No
   temporary copy of the whole backup is needed.
5. **Verify** the finished archive by reading it back end to end against the
   manifest's checksums.
6. **Keep rules** delete old archives, except any archive that is the last good
   copy of some database.
7. **Alerts** report failures, damaged databases, low disk space and missed runs.

## 4. Technology stack

Chosen to match ReelHaven, which this owner already runs and maintains
(ADR-0002).

| Part | Choice |
|---|---|
| Backend | Python 3.13, FastAPI, Pydantic, SQLAlchemy + Alembic, uv |
| Own database | SQLite in `/config` |
| Frontend | React + TypeScript, Vite, Mantine |
| Archive | `tar` (POSIX pax format) streamed through zstd (multi-threaded) |
| Recipes | YAML, validated against a versioned JSON Schema |
| Plugin sandbox | Separate process, unprivileged user, Landlock + seccomp; JSON messages over stdin/stdout (ADR-0004) |
| Container control | Optional `linuxserver/socket-proxy`, list/stop/start only (ADR-0005) |
| Image | Debian slim based, built and published by GitHub Actions to GHCR |

## 5. Container layout

### 5.1 Paths

| Path in container | Host (Unraid default) | Access | Purpose |
|---|---|---|---|
| `/config` | `/mnt/user/appdata/backuphaven` | read/write | BackupHaven's own settings, database, plugin store, secret key |
| `/mnt/user/appdata`, `/mnt/user/fast`, ... | the same path | read/write (ADR-0007) | **Data shares**: every share that holds container data, each mounted at its own host path (ADR-0009). Written **only** during a restore the owner starts |
| `/boot` | `/boot` | **read-only** | Unraid container templates; optional flash backup |
| `/backups` | `/mnt/user/backups/backuphaven` | read/write | Where archives are written (the built-in local destination) |

Data shares are mounted at the **same path inside the container as on the
host**, so folder paths read from the Unraid templates can be used as they are.
The Unraid template ships with `/mnt/user/appdata` and an "Add another data
share" note; the owner adds others (e.g. a cache-only share for speed).

BackupHaven never backs up its own `/backups` folder, and skips its own `/config`
plugin store and caches. Its own settings database *is* backed up (as an app
called BackupHaven), so a restored server gets its backup settings back too.

### 5.2 Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `TZ` | `Etc/UTC` | Time zone for the schedule |
| `BACKUPHAVEN_PORT` | `7272` | Web UI port |
| `BACKUPHAVEN_DOCKER_PROXY` | (empty) | Address of the socket proxy, e.g. `tcp://socket-proxy:2375`. Empty = stop mode unavailable |
| `PUID` / `PGID` | `99` / `100` | Owner of files BackupHaven creates in `/config` and `/backups` |

### 5.3 Process model and privileges

- One container, one main process (API, scheduler and run engine), plus one
  short-lived process per program-plugin call.
- The main process runs as **root with a reduced set of Linux capabilities**:
  only those needed to read every app's files and restore them with their
  original owners and permissions (`CAP_DAC_OVERRIDE`, `CAP_CHOWN`,
  `CAP_FOWNER`, `CAP_FSETID`, `CAP_SETUID`, `CAP_SETGID`); everything else is
  removed from the bounding set by the entrypoint, with `no_new_privs`. See
  ADR-0006 and SECURITY.md.
- Program plugins run as an unprivileged user with no capabilities, locked in
  by Landlock and seccomp (ADR-0004).

## 6. Components

### 6.1 Discovery

- Reads Unraid's user templates (read-only). From each template: container name,
  image, and the host paths of its volume mappings that point into appdata.
- Host paths are normalised to one form per share: `/mnt/cache/<share>/...`,
  `/mnt/<pool>/<share>/...` and `/mnt/diskN/<share>/...` all become
  `/mnt/user/<share>/...` (they are the same files on Unraid).
- A mapping counts as the app's data when it is inside a **mounted data share**.
  Mappings into other shares (media, downloads, `/tmp`) are ignored.
- A mapping of a whole share (e.g. `/mnt/user/media` → `/media`) is never app
  data. A mapping "looks like app data" when its container path ends in `config`
  (`/config`, `/app/config`, `/root/.config`...).
- If a mapping looks like app data but its share isn't mounted, the app is flagged **"data share not added"**, with the
  exact path to add to BackupHaven's template. It is never silently skipped.
- One app can have folders on several shares (e.g. config on `fast`,
  a database on `appdata`); all of them are backed up.
- With the socket proxy configured, also asks Docker which containers exist and
  which are running, and warns about templates whose container is gone.
- Apps are backed up only once they are added to the **backup schedule**, which
  is empty by default (ADR-0011). New apps show up on the Apps page.
- An app in a run whose data folders no longer exist (its container was removed
  but Unraid kept the template) is **skipped**, not failed, and listed at the end
  of the run's results (ADR-0011).

### 6.2 Plugin host

The single place where plugins meet the rest of the system. Every request a
plugin makes, recipe or program, goes through the same checks:

- **Scope**: an app plugin can only touch its own app's appdata folders.
- **Permissions**: a program plugin can only use what the owner approved.
- **Paths**: every path is resolved with `realpath` (symlinks first) and
  refused if it leaves the allowed folder.

Built-in actions available to plugins (plugin API v1):

| Action | What it does |
|---|---|
| `files.include` / `files.exclude` | Choose files and folders (glob patterns), relative to the app's folder |
| `sqlite.snapshot` | Consistent live copy using SQLite's online backup API |
| `sqlite.check` | `integrity_check` (or `quick_check` for very large databases) on the copy |
| `files.expect` | Fail the app's backup if a required file is missing |
| `container.stop` / `container.start` | Only the plugin's own container, only with the proxy configured |
| `http.request` | Program plugins only, only to approved hosts (e.g. ask an app to make its own backup first) |
| `archive.write_stream` | Destination plugins only: receive the finished, encrypted archive |
| `secret.get` | Only the plugin's own approved secrets |

### 6.3 Plugins

Three kinds, all described by a **plugin manifest** (`plugin.yaml`):

| Kind | Purpose | v1 form |
|---|---|---|
| **App** | Back up and restore one kind of container | Recipe (most) or program |
| **Destination** | Store archives somewhere | Built-in local folder; rclone and others as program plugins |
| **Alert** | Send notifications | Built-in set first; program plugins later |

Every manifest has: `id`, `name`, `version`, `kind`, `plugin_api` (version of
the action set it was written for), `author`, and either recipe steps or a
program entry point plus its requested `permissions`.

**Recipe example (Sonarr):**

```yaml
id: sonarr
name: Sonarr
version: 1.0.0
kind: app
plugin_api: 1
recognise:
  images: ["*/sonarr", "sonarr"]
backup:
  mode: snapshot
  databases: ["sonarr.db"]
  include: ["config.xml", "Backups/"]
  exclude: ["logs/", "logs.db*", "MediaCover/"]
  expect: ["config.xml"]
settings:
  - id: covers
    label: Include posters
    type: boolean
    default: false
    when_true: { include: ["MediaCover/"] }
restore:
  stop_first: true
```

**Bundled plugins in v1:** generic, Sonarr, Radarr, Prowlarr, Lidarr/Readarr
(as fitting), Plex, SABnzbd, qBittorrent, Home Assistant (container), MariaDB
and PostgreSQL (stop mode until a dump plugin exists), BackupHaven itself.
The exact list is set per phase.

**The generic plugin** backs up the app's whole appdata folder, finds SQLite
databases by their file header (`SQLite format 3\0`) and snapshots and checks
them. It skips common cache and log folders. Apps without a dedicated plugin are
therefore never unprotected.

**Program plugins** speak a small JSON message protocol over stdin/stdout to the
plugin host (one request, one reply). They can be written in any language. They
get **no** direct access to files, network or Docker; everything is a request to
the host. Details in ADR-0004.

### 6.4 Plugin installation and checks

- Bundled plugins ship inside the image and are always trusted.
- Other plugins are installed on purpose (from a catalog or by address). The
  install screen shows, in plain words, every permission the plugin asks for.
- Before first use, BackupHaven runs **install checks** as warning lights:
  - a static scan for warning signs (hidden/encoded code, process execution,
    network addresses not in its permissions, known-vulnerable dependencies);
  - a **test run** in the sandbox against sample data; any attempt to go beyond
    its permissions blocks the plugin.
- An update that asks for **new** permissions must be approved again.
- Catalog plugins are signed; the signature and checksum are verified on install
  and update.
- These checks can warn but cannot prove a plugin is harmless; the sandbox is
  what enforces safety (ADR-0004).

### 6.5 Run engine

- One run at a time. A run that is still going when the next is due is not
  doubled; the missed run is reported.
- Apps are processed one at a time in a stable order (configurable; stop-mode
  apps can be grouped so dependent containers stop together and start in
  reverse order).
- A failure in one app doesn't stop the run: the app is marked failed, the
  others continue, and the archive records what's missing.
- Stop mode only restarts containers that were running before, never stops
  BackupHaven or the socket proxy, and always tries to start the container
  again even if the copy failed. A container that won't start is an alert.
- Free space is checked before and during the run; below the limit the run stops
  cleanly and alerts.

### 6.6 Archive format

```
backuphaven-2026-10-17T0300.tar.zst
  manifest.json                      written last, also saved next to the archive
  apps/sonarr/files/<folder>/...     the app's files, one subfolder per data folder
                                     (e.g. `config`), paths relative to it
  apps/sonarr/databases/sonarr.db    snapshot copies
  apps/sonarr/template.xml           its Unraid template
  apps/plex/...
  flash/...                          optional Unraid flash backup
```

- `tar` in POSIX pax format keeps owners, permissions, times and long names.
- zstd compression, multi-threaded, level configurable (default 3).
- The manifest lists every file with size and SHA-256, each database check's
  result, each app's plugin and version, and the run's log.
- A copy of the manifest is stored beside the archive
  (`...tar.zst.manifest.json`), so the Backups page can list contents without
  opening archives.
- Optional encryption (later phase) wraps the whole archive with `age` using the
  owner's password; destination plugins only ever see the encrypted file.

### 6.7 Verifier

An archive is marked **good** only after it has been read back in full: the
zstd stream decodes, the tar is complete, and every file's checksum matches the
manifest. Database checks ran before writing (on the copies), and their results
are part of the manifest. A run whose archive fails verification is a failed
run, and its archive is not counted as anyone's last good copy.

### 6.8 Keep rules

A pure function with tests: given the list of archives (date, good/failed, and
which databases were healthy in each), decide which to delete.

- Defaults: last 7 nights, plus 4 weekly and 6 monthly.
- Never delete the last good copy of any database, even if it is older than the
  rules would keep. The Dashboard says when this is holding an old archive.
- Never delete the newest good archive.
- Failed archives are deleted after the next good one.

### 6.9 Restore

- Per app: choose an archive, see what's in it, click **Restore**.
- Without the proxy: BackupHaven asks the owner to stop the container in Unraid
  first and checks that the app's files aren't changing; with the proxy, it stops
  and starts the container itself.
- The app's current folder is moved aside to
  `<share>/.backuphaven-restore/<app>-<time>/` on the same share first (a fast
  rename, no copy), so a restore can be
  undone from the UI. Kept for 7 days (configurable).
- Owners, permissions and times are restored as they were in the archive.
- Also: download one app's files, or one file, as a separate archive.

### 6.10 Alerts

Failed run, damaged database found, archive failed verification, disk nearly
full, no good backup for more than 36 hours, container didn't come back after
stop mode. Optional morning summary. Channels: built-in ntfy, Discord, email,
Home Assistant webhook; more as alert plugins.

### 6.11 Scheduler

Runs inside the app (no cron). Daily at a set time (default 03:00) or weekly on
chosen days. **Run now** button. Missed runs (server off) are run at the next
start if more than a set time has passed (default: off, alert only).

## 7. Settings (owner-facing)

- **Apps**: add to / remove from the backup schedule (ADR-0011), plugin, mode (snapshot/stop), the plugin's own
  settings, extra include/exclude patterns, order.
- **Schedule**: time, days, Run now.
- **Keeping backups**: nightly/weekly/monthly counts, minimum free space.
- **Where backups go**: local folder (default `/backups`); later, a second
  destination plugin; compression level; encryption password (later phase).
- **Alerts**: channels and which events.
- **Plugins**: installed plugins, permissions, updates, catalog.
- **Security**: login, sessions, API key (as ReelHaven).

## 8. Data model (initial)

| Table | Holds |
|---|---|
| `apps` | Discovered containers, template path, data folders (share + path), plugin id, enabled, mode, settings JSON, order |
| `plugins` | Installed plugins, version, kind, source, signature status, approved permissions |
| `runs` | Each run: start/end, status, archive path, size, verification result |
| `run_apps` | Per run and app: status, files, bytes, duration, errors |
| `db_checks` | Per run and database: path, size, check result, time taken |
| `restores` | Each restore: app, archive, moved-aside folder, status, undo state |
| `settings` | Key/value settings |
| `users`, `sessions`, `audit_log` | Same model as ReelHaven |

## 9. API and UI

### 9.1 API

REST under `/api/v1`, JSON, session cookie or API key, same conventions as
ReelHaven. A WebSocket pushes run progress.

### 9.2 UI pages

| Page | Contents |
|---|---|
| **Dashboard** | Last run (status, size, time), next run, Run now, database health per app, storage and free space, anything that needs attention at the top |
| **Backups** | One row per archive: date, result, size, checks. Open one: browse contents, download, restore an app |
| **Apps** | Every container found: in the backup schedule or not (**Add to backup schedule**), plugin, mode, last size and checks; open one for its settings |
| **Plugins** | Installed plugins, their permissions, updates; install from the catalog |
| **Settings** | Schedule, keeping backups, where backups go, alerts, security |
| **Guide** | Built-in user guide, from `docs/guide/` |

First start opens a **setup wizard**: create the admin account, choose apps,
choose where backups go, choose the time, run the first backup.

## 10. Logging

JSON logs to stdout (Unraid shows them in the container log), secrets redacted.
Each run's log is also saved in its manifest. Audit log for logins, settings
changes, plugin installs/permission approvals and restores.

## 11. Testing strategy

- Pure functions (keep rules, recipe validation, path scoping, discovery parsing,
  plugin permission checks) have thorough unit tests.
- Tests build their own fixtures: fake appdata folders, SQLite databases created
  on the fly (healthy, damaged, being written to during the snapshot), fake
  Unraid templates. **Real app data is never committed.**
- Sandbox tests: a deliberately misbehaving test plugin tries to read outside
  its scope, open network connections and run commands; every attempt must be
  refused and reported.
- End-to-end: back up a fixture appdata, verify, restore into a temp folder,
  compare byte for byte including owners and permissions.
- Every bundled recipe has a test with a matching fixture.
- CI runs lint, types, tests, image build and a container smoke test.

## 12. Build and release

As ReelHaven: GitHub Actions builds and tests on every PR; `main` publishes
`:edge`, tags `vX.Y.Z` publish `:X.Y.Z` and `:latest` to GHCR. Signed commits,
conventional commit messages, squash merges. An Unraid template XML is kept in
the repo for Community Applications.

## 13. Roadmap

| Phase | Contents |
|---|---|
| **0.1 Foundation and engine** | Container, login, own database; plugin manifest and recipe loader; discovery from Unraid templates; SQLite snapshot and check; archive writer and verifier; generic, Sonarr, Radarr, Prowlarr and Plex plugins; Run now; basic status page; sandbox feasibility test on Unraid |
| **0.2 Every night** | Scheduler, keep rules, Dashboard, Backups and Apps pages, free-space guard |
| **0.3 Restore** | Browse, download, restore an app with undo |
| **0.4 Alerts** | Built-in alert channels, missed-run detection, morning summary |
| **0.5 Stop mode** | Socket proxy support, per-app stop mode, one-click restore, MariaDB/PostgreSQL plugins |
| **0.6 Program plugins** | Sandbox, plugin host messages, permissions screen, install checks, signing; rclone destination plugin; encryption |
| **0.7 Setup and extras** | Setup wizard, flash backup, second destination, plugin catalog |
| **1.0** | Hardening, full guide, plugin author guide, Community Applications listing |

## 14. Open questions

1. Repository public (like ReelHaven, free CodeQL scanning) or private until it
   works? Images must be public for Unraid to pull without logging in.
2. Does Unraid's Docker allow Landlock and the needed seccomp calls inside an
   ordinary container? To be tested first in 0.1 (fallback: WebAssembly
   plugins, ADR-0004).
3. Plex's database uses a custom collation; does `integrity_check` need a stub
   collation registered? Test against a copy of a real Plex database in 0.1.
4. Should the wizard offer to read the list of shares from Unraid (via `/boot`
   config) to suggest which data shares to add?
5. Very large SQLite databases: is `quick_check` needed above some size to keep
   nightly runs short?
