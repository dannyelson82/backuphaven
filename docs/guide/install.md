---
title: Installing
order: 10
summary: Add BackupHaven to your Unraid server and give it the folders it backs up.
---

# Installing

BackupHaven runs as one Docker container. It needs to see:

| Folder in BackupHaven | Folder on Unraid | Why |
|---|---|---|
| `/config` | `/mnt/user/appdata/backuphaven` | BackupHaven's own settings |
| `/backups` | a share for backups, e.g. `/mnt/user/backups/backuphaven` | Where the backup files go |
| `/boot` | `/boot` (**read only**) | The list of your containers and their settings |
| the **same path** as on Unraid | each share that holds app data, e.g. `/mnt/user/appdata` | The apps' data it backs up |

## Adding the container

BackupHaven isn't in Community Applications yet, so its template is added by
hand once:

1. Open Unraid's **Terminal** (the `>_` icon at the top right) and paste:

   ```
   wget -O /boot/config/plugins/dockerMan/templates-user/my-BackupHaven.xml https://raw.githubusercontent.com/dannyelson82/backuphaven/main/unraid/backuphaven.xml
   ```

   This downloads the template into the folder where Unraid keeps your own
   templates.
2. Go to **Docker** → **Add Container**, and choose **BackupHaven** under
   **Template** → **User templates**.
3. Check the paths (see the table above), add your other data shares (see below),
   then press **Apply**.

## Data shares

Some servers keep all app data in `appdata`. Others also have a second share,
for example one on the cache drive for speed. Add **every** share that holds
app data:

1. **Add another Path, Port, Variable, Label or Device** → **Path**.
2. **Name**: e.g. `Data share: fast`; **Container Path** and **Host Path**: the
   **same** path, e.g. `/mnt/user/fast` on both sides; **Access Mode**:
   Read/Write.

The same path on both sides matters: BackupHaven reads where each app keeps its
data from Unraid's templates, and finds it at that exact path. If an app in the
backup schedule keeps data on a share you haven't added, the Dashboard says
which one.

## First steps

1. Open `http://<your server>:7272` and create the admin account (see
   [Logging in](logging-in.md)).
2. Go to **Apps** and press **Add to backup schedule** for each app you want
   backed up (see [Apps and the backup schedule](apps.md)). The schedule starts
   empty.
3. Press **Run now** on the Dashboard for a first backup, and check the result.
4. Optionally, change when the nightly backup runs and how many old backups are
   kept in [Settings](settings.md).

## Updating

BackupHaven shows **Update ready** on the Docker page like other containers:
click its icon and choose **Update** (or **Force Update**). Your settings and
backups are kept.
