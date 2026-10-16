# 0012: Four-part version numbers

- Status: accepted (owner, 2026-10-16)
- Date: 2026-10-16

## Context
Releases were going to use the usual three-part numbers (0.2.0). The owner asked
for four-part version numbers, with the phase 0.2 release numbered **0.0.2.0**
("I want 4 digit versioning").

## Decision
Versions are `W.X.Y.Z`:

| Part | Goes up for |
|---|---|
| `W` | Major release: 1 at the first stable release (roadmap 1.0) |
| `X` | Feature releases after 1.0 |
| `Y` | Before 1.0: the roadmap phase (phase 0.2 is `0.0.2.0`, phase 0.3 `0.0.3.0`) |
| `Z` | Fix releases of the same features (`0.0.2.1`) |

- Git tags are `v` plus the version (`v0.0.2.0`); images are tagged with the
  version and `latest`.
- The Python package carries the same number (PEP 440 allows four parts).
- The frontend's `package.json` is private and never published; it keeps
  `0.0.0`, because npm only accepts three-part versions. The app shows the
  backend's version.
- Roadmap phases keep their short names (phase 0.2, 0.3…) in the docs.

## Consequences
- The image workflow can't use the semver tag rule; it matches `v*` tags with
  four numeric parts and tags `latest` itself.
- Tools that assume semver (Dependabot, some badges) don't apply to our own
  version; nothing currently depends on that.
