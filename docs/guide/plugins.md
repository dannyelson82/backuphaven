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
`lscr.io/linuxserver/sonarr` uses the Sonarr plugin. Containers without their
own plugin use **Any app**, so nothing is left unprotected.

## Included plugins

| Plugin | Backs up | Skips by default | Settings |
|---|---|---|---|
| **Any app** | Every data folder of the app. Databases are found automatically and checked. | Caches, logs, temporary and transcode folders | **Include logs** |
| **Sonarr**, **Radarr** | Settings (`config.xml`), the database, security keys | Logs, posters (`MediaCover`), the app's own backup zips | **Include posters**, **Include … own backups** |
| **Prowlarr** | Settings, the database, indexer definitions | Logs, the app's own backup zips | **Include Prowlarr's own backups** |
| **Plex Media Server** | Settings (`Preferences.xml`) and the databases: libraries, watch history, play positions | Posters, thumbnails, caches, logs, Plex's own dated database copies | **Include posters and metadata (large)** |
| **BackupHaven** | BackupHaven's own settings, so a rebuilt server gets them back | Logs. Its backup files are never inside its own backup. | |

Skipped items are rebuilt by the app itself after a restore. For Plex, that
means posters and artwork download again over a while.

## Databases

Databases (SQLite, used by Sonarr, Radarr, Prowlarr, Plex and many others) are
copied with SQLite's own live-backup method, so the app keeps running and the
copy is consistent. Every copy is then checked for damage.

Coming later: plugins written by other people, installed from a catalog, each
with the permissions you approve.
