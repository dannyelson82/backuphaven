# BackupHaven user guide (source)

These pages are the user guide. A future **Guide** section in the app will
render them, so they are written for the person using BackupHaven, not for
developers.

## Rules for writing and updating

- Every pull request that adds or changes something a user sees or
  configures updates the matching page here (see CLAUDE.md).
- Plain language. Use the **exact labels** shown in the UI, in bold.
- Say what a feature does, what the safe default is, and what can go wrong.
- Don't describe features that don't exist yet. A short "Coming later" note is
  fine when it prevents confusion.
- One topic per file. Each file starts with front matter:

  ```yaml
  ---
  title: Restore           # page title in the Guide
  order: 70                # position in the Guide's menu (gaps of 10)
  summary: One sentence shown in the Guide's index.
  ---
  ```

- Link to other pages by file name, e.g. `[Apps](apps.md)`.

