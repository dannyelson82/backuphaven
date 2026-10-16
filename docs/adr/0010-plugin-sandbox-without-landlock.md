# 0010: Program plugin sandbox on Unraid: seccomp lock-down (no Landlock)

- Status: accepted (owner, 2026-10-16)
- Date: 2026-10-16
- Amends: ADR-0004

## Context
ADR-0004 planned to lock program plugins in with **Landlock** (limit files and
network) plus **seccomp** (limit system calls), and to test this on Unraid first.
The test, run in an ordinary container on the owner's Unraid 7.3.2 server
(kernel 6.18.38-Unraid), found:

- Landlock: not available. `landlock_create_ruleset` returns "not implemented",
  and `/sys/kernel/security/lsm` doesn't exist on the host: the kernel is built
  without Linux security modules, so Landlock can't be switched on.
- seccomp: works. A process can install its own filter (after `no_new_privs`).
- User namespaces: blocked by Docker's default profile, as expected, so
  bubblewrap-style sandboxes are out.

Without Landlock, seccomp can still block whole system calls (open files,
connect, start programs) but can't allow "only this folder", because seccomp
filters can't look at file paths.

## Options

**A. seccomp lock-down before the plugin's code runs (recommended).**
1. BackupHaven starts a small, trusted bootstrap (part of BackupHaven) as an
   unprivileged user, with resource limits and a clean environment.
2. The bootstrap reads the plugin's files into memory, the plugin's own code
   only, never executed yet, and imports the few standard modules plugins may
   use.
3. It installs a seccomp filter that allows only what a running Python needs
   (memory, threads, clocks, reading and writing the two message pipes) and
   **refuses opening any file, any network socket, starting programs, and
   changing users**, permanently.
4. Only then does it run the plugin's code, loaded from memory.

The plugin can still compute anything, but everything that touches the outside
world (files, network, containers, secrets) must be a request to the plugin
host, which checks it against the permissions the owner approved, as in
ADR-0004.

Limits: plugins are **Python only, without compiled extensions** (they can't be
loaded after the lock-down). Plugins get a small helper library for talking to
the host (HTTP requests, reading their app's files, storing secrets).

**B. WebAssembly.** Plugins are compiled to WebAssembly and run in a WebAssembly
runtime inside BackupHaven. They can touch nothing unless the host passes it in.
Strong, independent of the kernel, and plugins can be written in Rust, Go,
JavaScript and others. Heavier for plugin authors (special build tools), and
Python plugins become large and slow to start.

## Decision
Option A. It keeps plugins easy to write (ordinary Python), and the security
promise is the same: a plugin can only do what the owner approved, whatever its
code contains. WebAssembly stays possible later as a second plugin type if
there's demand for other languages.

Sandbox tests (ADR-0004) become: a test plugin that tries to open files, connect
to the network, start a program, change user, or fork must be refused in every
case, and that must hold in CI and in the owner's container.

## Consequences
No dependence on kernel features Unraid doesn't have. Plugin authors are
limited to Python without native extensions; most destination plugins only
need HTTP, which the host provides. The seccomp allow-list must be maintained
as Python versions change; CI runs the refusal tests on every change.
