---
title: Plugins
order: 40
summary: What BackupHaven backs up for each app, and the settings each plugin offers.
---

# Plugins

BackupHaven learns about each app from a **plugin**. A plugin says which files
hold the app's settings and databases, what can be skipped (logs, caches,
pictures the app downloads again), and how to restore the app.

Each container is matched to a plugin by its image name, for example
`lscr.io/linuxserver/sonarr` uses the Sonarr plugin. **An app is backed up only
by a plugin made for it.** A plugin promises a complete copy: restoring it brings
the app back in working order. Apps without one are marked **No plugin yet** on
the Apps page and can't be added to the backup schedule.

Every backup also checks the plugin's work: a data folder of the app that the
plugin doesn't cover is reported on the app's result (**Warnings**), so nothing
goes missing quietly.

## Included plugins

| Plugin | Backs up | Skips by default | Settings |
|---|---|---|---|
| **Sonarr**, **Radarr** | Settings (`config.xml`), the database, security keys | Logs, posters (`MediaCover`), the app's own backup zips | **Include posters**, **Include … own backups** |
| **Prowlarr** | Settings, the database, indexer definitions | Logs, the app's own backup zips | **Include Prowlarr's own backups** |
| **Plex Media Server** | Settings (`Preferences.xml`) and the databases: libraries, watch history, play positions | Posters, thumbnails, caches, logs, Plex's own dated database copies | **Include posters and metadata (large)** |
| **Audiobookshelf** | The database (users, libraries, listening progress) and the metadata folder (covers, author pictures) | Cache, logs, Audiobookshelf's own backup files. The audiobooks themselves are media. | **Include Audiobookshelf's own backups** |
| **Cantinarr** | Settings and the database | Logs | |
| **Chaptarr** | Settings and the databases | Logs, its log and cache databases, posters, its own backup zips | **Include Chaptarr's own backups** |
| **PlanHaven** | Settings, secrets, PlanHaven's own nightly database dumps (`backups/`), and the attachments share (`/data`) | The live database files (`pgdata`), logs | |
| **BackupHaven** | BackupHaven's own settings, so a rebuilt server gets them back | Logs. Its backup files are never inside its own backup. | |

Skipped items are rebuilt by the app itself after a restore. For Plex, that
means posters and artwork download again over a while.

## PlanHaven

PlanHaven keeps its data in its own PostgreSQL database. Its files can't be
copied safely while PlanHaven runs, but PlanHaven writes a safe copy (a
*dump*) of the database every night into `appdata/planhaven/backups`, and
BackupHaven backs those up. If no dump is found, PlanHaven's backup fails, so a
problem with PlanHaven's own dumps doesn't go unnoticed.

PlanHaven's attachments (photos, documents) live in a share of their own,
usually `/mnt/user/planhaven`. Add that share to BackupHaven's container like
any other data share (see [Installing](install.md#data-shares)); until then the
Dashboard says it's missing.

**Restoring PlanHaven** puts back its settings, its dumps and its attachments.
The live database is left as it is. To also roll the database back, load a dump
with PlanHaven's own command, then restart PlanHaven:

```
docker exec planhaven /etc/s6-overlay/scripts/restore-db /config/backups/planhaven-<date>-<time>.dump
```

## Databases

Databases (SQLite, used by Sonarr, Radarr, Prowlarr, Plex and many others) are
copied with SQLite's own live-backup method, so the app keeps running and the
copy is consistent. Every copy is then checked for damage.

Other database servers (PostgreSQL, MariaDB/MySQL) found in an app's folders
can't be copied safely while the app runs. The backup marks that app
**Warnings** and says so; keep the app's own database dumps until stop mode
arrives.

## Trying a plugin before it's released

Put a plugin file (`<name>.yaml`) in the `plugins` folder inside BackupHaven's
appdata folder, usually `/mnt/user/appdata/backuphaven/plugins/`. BackupHaven
picks it up straight away, without a restart, and marks the app **Local
plugin**. A local plugin with the same name as an included one replaces it, so an
improved plugin can be tried too. Files that aren't valid plugins are ignored and
logged.

Coming later: plugins written by other people, installed from a catalog, each
with the permissions you approve.
