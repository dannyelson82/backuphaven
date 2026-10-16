---
title: Installing
order: 10
summary: Add BackupHaven to your Unraid server and give it the folders it backs up.
---

# Installing

> **Early test build.** BackupHaven doesn't back anything up yet. Install it
> only if you're helping test it.

BackupHaven runs as one Docker container. It needs to see:

| Folder in BackupHaven | Folder on Unraid | Why |
|---|---|---|
| `/config` | `/mnt/user/appdata/backuphaven` | BackupHaven's own settings |
| `/backups` | a share for backups, e.g. `/mnt/user/backups/backuphaven` | Where the nightly backup files go |
| `/boot` | `/boot` (**read only**) | The list of your containers and their settings |
| the **same path** as on Unraid | each share that holds app data, e.g. `/mnt/user/appdata` | The apps' data it backs up |

## Data shares

Some servers keep all app data in `appdata`. Others also have a second share,
for example one on the cache drive for speed. Add **every** share that holds
app data, using the **same path** on both sides: `/mnt/user/appdata` on Unraid
becomes `/mnt/user/appdata` in BackupHaven.

Coming later: BackupHaven will tell you if an app keeps data on a share you
haven't added yet.

## Opening it

Open `http://<your server>:7272` in a browser. The early test build shows its
version and a short note. More pages arrive with each part of version 0.1.
