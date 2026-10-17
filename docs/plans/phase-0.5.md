# Phase 0.5: Databases in other containers, and stop mode

Status: **approved by the owner on 2026-10-16** ("combine stop mode as well").
Released as **0.0.5.0**.

Goal: apps whose database lives in another container (RomM with MariaDB) are
backed up and restored together with it, consistently and without stopping
anything (ADR-0014); and, with the optional socket proxy (ADR-0005), any app can
be stopped during its backup, restores stop and start the container themselves,
and BackupHaven knows which containers really exist.

## Chunks (one pull request each)

| # | Chunk | Contents |
|---|---|---|
| 1 | **Database tools and dump engine** | MariaDB and PostgreSQL clients in the image; dump and load with checks (completion marker, exit codes, timeouts), passwords via environment; tests against real servers (CI service containers) |
| 2 | **External databases in plugins** | `backup.external_databases` with `${TEMPLATE_VAR:-default}` values; per-app database address override (Apps page); dumps stored in the app's backup; restore loads them, with a pre-restore dump for Undo |
| 3 | **Plugins** | RomM (MariaDB), the MariaDB and PostgreSQL containers themselves (all databases); fixtures extended for external databases |
| 4 | **Socket proxy** | Client for `linuxserver/socket-proxy` (list, stop, start only); container status on the Apps page (running, stopped, not installed: stale templates found exactly); setup guide (private network, proxy container, BackupHaven setting) |
| 5 | **Stop mode** | Per-app **Stop during backup**; plugins can default to it; the container is stopped, copied, started again (always, even after an error; only if it was running; never BackupHaven or the proxy); an alert if it doesn't come back |
| 6 | **One-click restore** | With the proxy, a restore stops the container, restores and starts it again; without it, the current guided steps |
| 7 | **Release 0.0.5.0** | Version, notes, tag; owner sets up the proxy and tests RomM and stop mode |
