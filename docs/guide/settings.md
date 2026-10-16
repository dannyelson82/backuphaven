---
title: Settings
order: 50
summary: The Settings page, including the developer option for diagnostics files.
---

# Settings

Open **Settings** from the menu at the top of every page.

## Schedule

- **Back up automatically**: on by default. Switch it off to make backups only
  with **Run now**.
- **Time**: when the backup starts, in the server's time zone (shown under the
  box). The default is 03:00.
- **Days**: the days it runs. All days by default.

Press **Save**. The page then shows when the next backup is due. Nothing runs
until apps are in the [backup schedule](apps.md).

The time zone comes from the container's `TZ` setting, which Unraid fills in.
If the times look wrong, check **TZ** in BackupHaven's container settings
(under **Show more settings**).

## Keeping backups

- **Nightly**: how many days of backups to keep (7). If several backups were
  made on one day, the newest is kept.
- **Weekly**: how many weeks to keep one backup from (4).
- **Monthly**: how many months to keep one backup from (6).
- **Minimum free space**: in GB (2). Backups don't start below this.

Press **Save**. The new numbers are used after the next backup, when old backups
are cleared out. See [Old backups](backing-up.md#old-backups) for what is never
deleted.

## Developer

### Diagnostics files

**Diagnostics files** is off by default. Switch it on when you're tracking down
a problem, for example a backup that takes much longer than expected, or when
someone helping you asks for them. It writes:

| File | What's in it |
|---|---|
| `debug.log` | A detailed log of everything BackupHaven does: each app, folder and database, how long each step took. Kept under 1 MB; three older parts (`debug.log.1` to `.3`) are kept, older lines are removed. |
| `run-<number>.json` | A summary of one backup: every app, every database check and every timing. The 10 newest are kept. |
| `system.json` | BackupHaven's version, the shares it can see, its permissions and its settings. |

Passwords, API keys and session cookies are never written to these files.
They do contain folder and app names from your server, so look through them
before sharing them with anyone.

The Settings page shows the folder and the files in it. The setting is kept
when BackupHaven restarts. Switch it off when you're done; the files stay
until you delete them.

**Where the files go**: `diagnostics` inside BackupHaven's appdata folder
(usually `/mnt/user/appdata/backuphaven/diagnostics`). To put them somewhere
else, for example a share you can open from another computer, edit the
BackupHaven container and add a path:

1. **Add another Path, Port, Variable, Label or Device** → **Path**.
2. **Name**: `Diagnostics`, **Container Path**: `/diagnostics`, **Host Path**:
   the folder you want, **Access Mode**: Read/Write.
3. **Save**, then **Apply**.

BackupHaven uses `/diagnostics` whenever it's there.
