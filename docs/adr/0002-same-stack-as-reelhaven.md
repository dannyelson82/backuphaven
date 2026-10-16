# 0002: Same technology stack as ReelHaven

- Status: proposed
- Date: 2026-10-16

## Context
The owner already runs and maintains ReelHaven (Python/FastAPI, SQLite, React +
Mantine, uv, GitHub Actions to GHCR). A second stack would mean new tools, new
CI, and nothing to reuse.

## Decision
Python 3.13 + FastAPI + SQLAlchemy/Alembic + SQLite backend, React + TypeScript +
Vite + Mantine frontend, uv and npm, Debian slim image built by GitHub Actions.
ReelHaven's login, settings, setup wizard, page shell and Guide renderer are
copied in and adapted, not shared as a library.

## Consequences
Fast start and one set of conventions for both apps. Copies will drift apart
over time, which is acceptable for two small apps. Python isn't the fastest at
moving bytes, but the heavy work (zstd compression, SQLite backup) runs in
native code.
