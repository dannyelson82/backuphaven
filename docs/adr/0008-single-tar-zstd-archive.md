# 0008: One streamed tar + zstd archive per run, with a manifest

- Status: accepted
- Date: 2026-10-16

## Context
The owner wants everything in a single compressed file each night.

## Decision
Each run writes one POSIX pax `tar`, compressed with multi-threaded zstd and
streamed directly to the destination (no temporary full copy). Layout:
`apps/<app>/files/`, `apps/<app>/databases/`, `apps/<app>/template.xml`,
optional `flash/`, and `manifest.json` (every file with SHA-256, check results,
plugin versions, run log). A copy of the manifest is saved beside the archive.
Optional `age` encryption wraps the whole file later.

## Consequences
One file per night, readable with standard tools (`tar --zstd -xf`) even
without BackupHaven. Restoring one app means reading through the archive up to
that app; acceptable for nightly sizes, and a seekable zstd format can be added
later if needed. No deduplication: each archive is a complete copy.
