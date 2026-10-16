# Security

## Reporting a vulnerability

Please **do not open a public issue.** Use GitHub's private vulnerability
reporting: the "Report a vulnerability" button on this repository's Security
tab. You'll get a response as soon as practical.

## Threat model

BackupHaven runs on a home server's local network. It can read and write every
app's data, can stop and start containers (with the optional proxy), and holds
copies of every app's secrets inside its archives. It is therefore treated as
one of the most sensitive apps on the server.

What we protect against:

1. Someone on the network (or the internet, if exposed) controlling the app or
   downloading backups without logging in.
2. A **plugin** (from the catalog or installed by address) doing anything its
   owner didn't approve: reading other apps' data, sending data away, running
   commands, touching containers.
3. A malicious or oddly named file inside an app's folder causing BackupHaven to
   read or write outside that folder (symlinks, `..`, special files).
4. A malicious or damaged **archive** writing outside the app's folder during a
   restore.
5. Leaking secrets: BackupHaven's own (alert tokens, encryption password) and
   the apps' secrets contained in the archives.
6. Bugs destroying app data or good backups (treated as a security concern:
   see "Data safety").

Out of scope: an attacker who already has root on the Unraid host.

## Authentication and web protections

Same model as ReelHaven:

- Login required; the first-run wizard creates the admin. No default
  credentials. Until setup is done, everything except setup and `/healthz` is
  blocked.
- Argon2id password hashes; random 256-bit session IDs stored hashed;
  `HttpOnly`, `SameSite=Lax`, `Secure` over HTTPS; idle timeout; log out
  everywhere.
- Login throttling per username and per IP; lockouts audited.
- Optional local-address bypass, off by default; client IP from the socket
  unless the peer is a trusted proxy; never for the Docker gateway address.
- API key for scripts (`X-Api-Key`), stored hashed, shown once. The API key
  **cannot** install plugins, approve permissions, change security settings,
  download archives or start a restore.
- CSRF tokens for cookie-authenticated changes; strict security headers
  (CSP self only, nosniff, `same-origin` referrer, frame deny); WebSocket
  checks session and `Origin`; Pydantic validation, unknown fields rejected.
- **Downloading a backup or restoring re-asks for the password** (archives
  contain every app's secrets).

## Plugins

The core rule: **a plugin can only do what the owner approved, whatever its
code contains.** Checks of a plugin's code are warning lights; the sandbox is
what enforces safety (ADR-0003, ADR-0004).

### Recipes

- Recipes are data, not code: validated against the plugin API's schema, then
  run by BackupHaven's own built-in actions.
- An app recipe can only touch its own app's appdata folders; patterns that
  try to leave them (`..`, absolute paths) are rejected at validation.

### Program plugins

- Each call runs as a **separate process**:
  - as an unprivileged user (no root, no capabilities, `no_new_privs`);
  - **Landlock**: no file access except its own read-only plugin folder and a
    private temporary folder; no network connections or listening;
  - **seccomp**: a short allow-list of system calls; no `ptrace`, no mounts,
    no new namespaces, no raw sockets;
  - resource limits: CPU time, memory, file size, number of processes;
  - a clean environment: no inherited secrets, no Docker proxy address.
- The only channel to the outside is the plugin host (stdin/stdout messages).
  Every request is checked against the plugin's **approved permissions**.
- **Permissions** are declared in the manifest and shown in plain words before
  install, e.g. "Upload files to `*.backblazeb2.com`", "Read Sonarr's appdata",
  "Stop and start the Sonarr container", "Store one secret". Network
  permissions name hosts; requests are made by the host on the plugin's behalf
  and only to approved hosts over HTTPS.
- **Destination plugins only ever receive the finished archive**, encrypted
  when encryption is on. They never see appdata.
- **Install checks** (warnings, not proof): static scan for encoded or
  obfuscated code, process execution, unexpected network addresses and known
  vulnerable dependencies; a test run against sample data where any refused
  request blocks the plugin.
- An update that asks for new permissions must be approved again; until then
  the old version stays in use.
- Catalog plugins are signed. Signatures and checksums are verified on install
  and update. Plugins installed by address are labelled "unverified".
- A plugin that is refused something at run time is stopped, the attempt is
  logged with details and audited, and the owner is alerted.
- If the sandbox can't be set up on the host (kernel or Docker doesn't allow
  it), program plugins are **disabled**, never run unprotected.

## File system confinement

- BackupHaven reads only inside the mounted **data shares**, `/boot` (read-only
  mount) and its own `/config` and `/backups`; and within data shares, only the
  folders that belong to a discovered app.
- Every path, from plugins, the UI, the API or an archive, is resolved with
  `realpath` (symlinks first) and refused if it leaves its allowed root.
- Symlinks inside app folders are archived as links, never followed out of the
  folder. Device files, sockets and FIFOs are skipped.
- **Restores** extract only entries under the chosen app's prefix, refuse
  absolute paths, `..`, and links pointing outside the app's folder, and write
  to a fresh folder that is moved into place only when extraction succeeds.
- Data shares are writable only so restores can work (ADR-0007); outside a
  restore BackupHaven opens nothing in them for writing (enforced in the
  file-access layer and tested).

## Container control

- **No Docker socket.** BackupHaven never gets `/var/run/docker.sock`.
- Stop mode and one-click restore use the optional
  **`linuxserver/socket-proxy`**, configured to allow only listing containers
  and starting/stopping them (ADR-0005). No creating, removing, exec, images,
  volumes or networks.
- BackupHaven never stops itself or the proxy, and a plugin may only stop its
  own app's container.

## Privileges

- The main process runs as root inside the container, with all capabilities
  dropped except those needed to read every app's files and restore their
  owners (ADR-0006). Files BackupHaven creates for itself (`/config`,
  `/backups`) are owned by PUID:PGID.
- No privileged mode, no host networking, no devices.

## Secrets

- BackupHaven's own secrets (alert tokens, plugin secrets, encryption
  password) are encrypted at rest with a key generated on first run
  (`/config/secret.key`, `0600`).
- Never returned in full by the API, never logged (redaction filter), never in
  diagnostics exports.
- **Archives contain the apps' secrets** (API keys in `config.xml` files,
  tokens). Archives are written `0600` and owned by PUID:PGID. Encryption
  (later phase) is recommended, and **required** for any destination outside
  the server.
- The encryption password is never stored in archives. Losing it means losing
  encrypted backups; the UI says so and offers a printable recovery sheet.

## Data safety

- An archive counts as good only after it is read back and every checksum
  matches.
- The keep rules never delete the last good copy of any database, or the newest
  good archive.
- Database checks run on the snapshot copies, never on live databases.
- Stop mode always tries to restart the container, and alerts if it doesn't
  come back.
- A restore moves the current files aside first and can be undone; restores and
  undo are audited.
- Runs stop cleanly before the backup disk fills up.

## Container hardening

- Minimal Debian slim base image, dependencies pinned, image scanned in CI.
- Read-only `/boot` mount; healthcheck on `/healthz`.

## Development practices

- Never commit secrets, real app data or backups. GitHub secret scanning with
  push protection is on.
- Signed commits required on `main`; Dependabot alerts and security updates on.
- Personal infrastructure details (server IPs, domains, container names) are
  never committed; setup-specific notes go in the gitignored `LOCAL_NOTES.md`.
