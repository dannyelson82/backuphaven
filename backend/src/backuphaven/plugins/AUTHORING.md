# Writing a BackupHaven plugin

BackupHaven backs up Docker apps on Unraid. **Each app is backed up only by a
plugin made for it** (ADR-0013), and a plugin promises one thing:

> **Restoring its backup brings the app back in working order.**

A plugin is a small YAML recipe: no code. BackupHaven does all the work (copying
files, copying SQLite databases safely while the app runs, checking them,
restoring); the recipe says what to take, what to skip, and what must be there.

## 1. Plugin file: `backend/src/backuphaven/plugins/bundled/<id>.yaml`

```yaml
id: myapp                    # lowercase letters, digits, "-" (also the file name)
name: My App                 # shown in the app
version: 1.0.0               # bump when you change the plugin
kind: app
plugin_api: 1
author: Your name
description: >-              # one or two sentences: what's backed up, what's skipped
  My App's settings and database. Logs and thumbnails are skipped.
recognise:
  # Image names without registry tag; globs. "*/myapp" matches
  # lscr.io/linuxserver/myapp and ghcr.io/someone/myapp.
  images: ["*/myapp", "myapp"]
backup:
  mode: snapshot             # "stop" arrives with stop mode (later); use snapshot
  # The container path(s) of the app's data folder(s), as in the app's Unraid
  # template: one ("/config") or several (["/config", "/metadata"]).
  folder: /config
  # Container paths mapped to a WHOLE share that holds the app's own data
  # (e.g. attachments on /mnt/user/myapp). Whole shares are otherwise ignored.
  share_folders: []
  # SQLite databases, as globs. They're copied with SQLite's live-backup API and
  # checked for damage. List each one; "auto" (find by file header) exists but a
  # list is better: it documents the app and lets BackupHaven skip folders.
  databases: ["myapp.db"]
  include: ["**"]            # usually everything...
  exclude:                   # ...minus what the app rebuilds or doesn't need
    - "logs/**"
    - "cache/**"
    - "**/*.log"
  # Files that must exist, or the backup fails (so a broken setup is noticed).
  # Checked across all of the app's folders.
  expect: ["config.xml", "myapp.db"]
settings:                    # optional on/off choices for the owner
  - id: thumbnails
    label: Include thumbnails
    help: Thumbnails can be large; My App makes them again.
    default: false
    when_true:
      include: ["thumbnails/**"]   # also lifts a matching exclude
restore:
  stop_first: true
```

**Patterns** are relative to each data folder, use `/`, and are matched against
the whole path: `*` is one level, `**` any number of levels, `logs/` means
`logs/**`. They can't start with `/` or contain `..`.

## 2. What to include, what to skip

- **Include** everything the app needs to start as it was: settings, databases,
  keys and certificates, users, uploaded files, plugins/addons it installed.
- **Skip** what the app rebuilds by itself: caches, logs, thumbnails, transcodes,
  downloaded artwork (offer an optional setting if it's slow to rebuild), PID and
  socket files, temporary folders.
- **The app's own backup files** (zips it makes itself): skip by default and
  offer a setting to include them.
- **Database servers** (PostgreSQL, MariaDB/MySQL) can't be copied safely while
  running. If the app makes its own dumps, exclude the live database folder and
  require the dumps with `expect` (see the PlanHaven plugin). Otherwise say so
  in the description; BackupHaven marks such backups **Warnings** until stop
  mode exists.
- **Media libraries** (films, music, books) are not app data: don't include
  them.
- On restore, files the backup leaves out on purpose are kept from the app's
  current folder, so excluding a cache never loses it.

## 3. Fixture: `backend/tests/plugin_fixtures/<id>.yaml`

A small, **made-up** sample of the app's folders. CI backs it up with your
plugin, deletes the folders as if the server were new, restores, and checks:

```yaml
plugin: myapp
image: lscr.io/linuxserver/myapp:latest     # must be recognised by the plugin
folders:
  - target: /config
    files:                       # path: text (invented, neutral content)
      config.xml: "<Config><ApiKey>example</ApiKey></Config>"
      logs/myapp.txt: "log line"
      thumbnails/1.jpg: "image"
    sqlite: [myapp.db]           # small SQLite databases created for the test
    links: {}                    # path: link target, if the app uses links
  # - target: /data
  #   whole_share: true          # for share_folders
must_restore: [/config/config.xml, /config/myapp.db]
must_not_backup: [/config/logs/myapp.txt, /config/thumbnails/1.jpg]
```

`must_restore` lists what makes the app work; `must_not_backup` what is skipped.
Use invented names and content only.

## 4. Check it

From `backend/`:

```
uv run python -m backuphaven.plugins check src/backuphaven/plugins/bundled/<id>.yaml \
    --fixture tests/plugin_fixtures/<id>.yaml
uv run pytest tests/test_plugin_fixtures.py tests/test_plugins.py
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

To try it on a real server before it's released, copy the plugin file to
`/mnt/user/appdata/backuphaven/plugins/<id>.yaml` (BackupHaven loads it straight
away, marked *Local plugin*), add the app to the backup schedule, run a backup,
and try a restore.

## 5. Open a pull request

Repository: <https://github.com/dannyelson82/backuphaven>

1. Branch `plugin/<id>` from `main`.
2. Add the plugin file and the fixture; add a row for the app to the table in
   `docs/guide/plugins.md` (what's backed up, what's skipped, settings).
3. Commit with a conventional message, e.g. `feat: My App plugin`.
4. Open a pull request titled `feat: <App> plugin` and fill in the plugin
   checklist (`.github/PULL_REQUEST_TEMPLATE/plugin.md`).

## 6. Rules

- **No personal details in the repository.** Use invented names and contents in
  fixtures; never copy share names, server paths, user names, library names or
  file names from someone's server.
- Recipes only: no scripts, no code, no network access.
- One plugin per app. If an app is published under several images, list them all
  in `recognise.images`.
