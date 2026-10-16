# 0007: Data shares are mounted writable, written only during restores

- Status: accepted
- Date: 2026-10-16

## Context
Restoring needs to write into app folders. A read-only mount would be safer, but
then restores would have to go to a side folder and be copied back by hand, at
the moment the owner is already dealing with a broken app.

## Decision
Data shares (ADR-0009) are mounted read/write. All writes go through one file-access layer
that only allows them inside a restore the owner started, inside that app's
folder. The current folder is moved aside first, so the restore can be undone.
Tests check that a normal backup run opens nothing in a data share for writing.

## Consequences
Restores are one click and can be undone. Mistakes in the file-access layer are
the main risk; see ADR-0006.
