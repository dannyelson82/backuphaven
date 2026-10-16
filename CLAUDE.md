# Instructions for Claude Code

BackupHaven is a simple, plugin-based, automated backup app for Unraid: every
night it backs up the Docker containers' data into one verified, compressed
archive. Before any work, read ARCHITECTURE.md and SECURITY.md; decisions are
recorded in docs/adr/.

## Rules
- Follow ARCHITECTURE.md and SECURITY.md. If something needs to change,
  propose a new ADR and wait for the owner's approval before implementing.
- Work in roadmap phase order (ARCHITECTURE.md §13). Plan each phase with the
  owner before writing its code. Phase plans live in `docs/plans/`.
- Workflow: one branch and pull request per chunk of a phase; CI must pass;
  Claude merges (squash) without waiting for review.
- Document every user-facing feature or change in `docs/guide/` (plain-language
  user guide, rules in `docs/guide/README.md`) in the same pull request.
- Keep changes small, with clear conventional commit messages
  (`feat:`, `fix:`, `docs:`, `test:`, `chore:`). All commits signed.
- Never commit secrets, real app data, backups, or personal infrastructure
  details (IPs, domains, container names). Setup-specific notes go in the
  gitignored LOCAL_NOTES.md.
- No sudo, no Docker socket. Images are built by GitHub Actions only.
- Every file access goes through the file-access layer: scoped to the app's
  folders, `realpath` checked, and no writes to data shares outside a restore.
- Plugins never get more than their scope and approved permissions; never add a
  shortcut around the plugin host, even for bundled plugins.
- Run external programs only via argument lists, never through a shell.
- Keep rules, discovery parsing, recipe validation and permission checks are
  pure functions with thorough tests. Tests build their own fixtures (fake
  appdata, generated SQLite databases); never commit real app data.
- The owner is comfortable following steps but is not a command-line expert:
  when they must do something themselves, give exact numbered steps and explain
  what each does. Discuss design before big decisions.
