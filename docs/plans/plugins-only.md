# Plugins only (ADR-0013)

Status: **approved by the owner on 2026-10-16**. Ships in the next release
together with the PlanHaven plugin (#34).

## Chunks (one pull request each)

| # | Chunk | Contents |
|---|---|---|
| 1 | **Plugin test kit** | A fixture format (`plugins/fixtures/<id>.yaml`: sample files, databases, folders); a test that backs up every bundled plugin's fixture, restores it into empty folders and compares; fixtures for the existing plugins; `python -m backuphaven.plugins check <file>` to validate a manifest |
| 2 | **Plugins for the owner's apps** | Audiobookshelf (metadata inside config counted once), Cantinarr, Chaptarr, each with a fixture |
| 3 | **Plugins only** | Remove the generic plugin; **No plugin yet** on the Apps page; schedule refuses apps without a plugin; runs report scheduled apps whose plugin is gone; completeness check (uncovered data folders, nested folders counted once) |
| 4 | **Plugin development file** | API + **Make a plugin** button: a Markdown brief with the app's mappings, a privacy-safe map of its folders, the plugin format and promise, fixture rules, and pull request steps; `docs/plugins/AUTHORING.md` and a pull request template for plugins |
| 5 | **Release** | Version, notes, tag |
