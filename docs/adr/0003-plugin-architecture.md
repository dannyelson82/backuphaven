# 0003: Everything app- or provider-specific is a plugin

- Status: accepted
- Date: 2026-10-16

## Context
The owner wants BackupHaven built around plugins: one per app to back up, with
all of that app's backup and restore settings, so other people can add apps and
storage providers (e.g. a custom cloud) later.

## Decision
- Three plugin kinds: **app**, **destination**, **alert**, each described by a
  `plugin.yaml` manifest with a `plugin_api` version.
- App plugins are normally **recipes**: YAML data run by BackupHaven's built-in
  actions (file selection, SQLite snapshot and check, stop/start). They can't do
  anything the actions don't allow.
- Any plugin may instead be a **program**, run sandboxed (ADR-0004) with
  owner-approved permissions. This is the normal form for destinations.
- A built-in **generic** app plugin covers any container without its own plugin.
- Official plugins are bundled in the image; a community catalog comes later.
- The plugin API is versioned. New actions bump the minor version; breaking
  changes bump the major version, and old plugins keep working until they're
  updated.

## Consequences
The core stays small, and adding an app usually means writing about 20 lines of
YAML. The action set must be designed with care, because it is a public
contract. Two plugin forms mean two paths to test, but both go through the same
plugin host checks.
