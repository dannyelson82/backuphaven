## Plugin: <App>

Image(s): `…`

### What it backs up / skips

- Backs up: …
- Skips (the app rebuilds them): …
- Settings: …

### Checklist (docs: backend/src/backuphaven/plugins/AUTHORING.md)

- [ ] `backend/src/backuphaven/plugins/bundled/<id>.yaml`, `id` matches the file name
- [ ] `backend/tests/plugin_fixtures/<id>.yaml` with invented names and contents only
- [ ] `must_restore` covers what the app needs to start as it was (settings, databases, keys, user data)
- [ ] `expect` lists the files without which the app can't be restored
- [ ] Database servers (PostgreSQL/MariaDB) handled through the app's own dumps, or noted in the description
- [ ] `uv run python -m backuphaven.plugins check … --fixture …` passes
- [ ] `uv run pytest tests/test_plugin_fixtures.py tests/test_plugins.py` passes
- [ ] A row for the app in `docs/guide/plugins.md`
- [ ] No server details (share names, paths, user or library names) anywhere
- [ ] Tried on a real server as a local plugin (optional, say which app version)
