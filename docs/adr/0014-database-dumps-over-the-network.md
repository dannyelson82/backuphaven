# 0014: Apps' databases in other containers are dumped over the network

- Status: accepted (owner, 2026-10-16)
- Date: 2026-10-16
- Amends: ADR-0003 (plugin format), ARCHITECTURE.md §6.2–6.3, §6.9, roadmap

## Context
Many apps keep their data in a database server running in another container:
RomM with MariaDB on the owner's server, Nextcloud, Immich and others with
MariaDB or PostgreSQL. Copying a database server's files while it runs may not
restore, and stopping a shared database stops every app that uses it. The
owner asked: "what about containers that use another db in a separate
container, app x using MariaDB in a separate container", and chose to do this
together with stop mode ("combine stop mode as well").

## Decision
- An app plugin can declare **external databases** (`backup.external_databases`):
  engine (`mariadb` or `postgres`) and where the address, database name, user
  and password come from, usually the app's own Unraid template variables
  (`${DB_HOST}`, `${DB_NAME}:-romm`…). The owner can override the address per app
  when the template's host isn't reachable from BackupHaven (e.g. a container
  name on another network).
- BackupHaven connects as a database client and dumps **only that app's
  database** with the server's own tool (`mariadb-dump --single-transaction`,
  `pg_dump`), consistent while everything keeps running. The dump is stored in
  the app's backup and checked (it must finish with the tool's completion
  marker). Passwords go to the tools through the environment, never the command
  line, and are never stored by BackupHaven.
- **Restore** loads the dump back into that database. Just before, BackupHaven
  dumps the current database, so **Undo** can load that back.
- A plugin for the **database container itself** (MariaDB, PostgreSQL) dumps
  every database with the administrator login from its template, for a full
  server copy.
- The image includes the MariaDB and PostgreSQL client tools (PostgreSQL's
  newest client, which can dump older servers).

## Consequences
- App plus database backups and restores without stopping anything, and without
  Docker access.
- The backup file holds the dumps; with app templates it may also hold database
  passwords, so encryption (0.6) matters before backups leave the server.
- Tests need real database servers: CI runs MariaDB and PostgreSQL as service
  containers; locally those tests are skipped when no server is available.
