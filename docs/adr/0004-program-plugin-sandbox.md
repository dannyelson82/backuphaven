# 0004: Program plugins run locked in, with approved permissions

- Status: proposed
- Date: 2026-10-16

## Context
The owner asked whether plugins can be programs, with BackupHaven testing them
for malicious code. Automatic analysis cannot reliably prove code is harmless:
obfuscation, time bombs, code fetched later, and detecting test conditions all
defeat it. BackupHaven itself runs with access to every app's data.

## Decision
- Safety comes from **containment**, not inspection. Each program-plugin call is
  a separate process: unprivileged user, `no_new_privs`, **Landlock**
  (filesystem: own folder read-only plus a private temp folder; network: none),
  **seccomp** allow-list, resource limits, and a clean environment.
- The plugin talks only to the **plugin host** with JSON messages over
  stdin/stdout, and the host checks every request against the owner-approved
  permissions.
- Destination plugins receive only the finished, encrypted archive.
- Install checks (static scan, test run on sample data, signature check for
  catalog plugins, re-approval when an update asks for new permissions) give
  warnings. They are not the gate.
- If the host can't provide the sandbox, program plugins are disabled.
- **Feasibility:** phase 0.1 tests Landlock and seccomp inside an ordinary
  Unraid container first. If that fails, plugins run as **WebAssembly**
  (e.g. Extism/wasmtime) instead: a stricter sandbox with the same permission
  model, but harder for plugin authors.

## Consequences
A malicious plugin can do at most what the owner approved. Plugins can't use
ordinary libraries that open files or connections themselves; they must ask
the host, so writing one takes a small helper library (provided for Python
first). Per-call process start-up costs a little time, which doesn't matter for
nightly runs.
