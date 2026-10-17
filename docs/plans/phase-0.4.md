# Phase 0.4: Alerts

Status: **approved by the owner on 2026-10-16** ("continue on"). Released as
**0.0.4.0** (ADR-0012).

Goal: BackupHaven tells the owner, without being asked, when something needs
attention, and (optionally) every morning how last night went.

## What raises an alert (ARCHITECTURE.md §6.10)

| Event | When |
|---|---|
| **Backup failed** | A run ends **Failed**: an app failed, a database is damaged, the backup file failed its check, or the disk filled up |
| **Database damaged** | A database check finds damage (also part of "backup failed", named separately so it stands out) |
| **Backup missed** | A scheduled backup didn't run (server off, another backup running) |
| **No good backup** | No good backup for more than 36 hours while apps are in the schedule (checked hourly) |
| **Disk nearly full** | Free space on the backups disk under twice the minimum (checked hourly) |
| **Restore failed** | A restore didn't finish (files were put back) |
| **App not protected** | An app in the backup schedule has no plugin any more |

The same alert isn't repeated within 12 hours. **Recovered** messages follow
when a problem clears (a good backup after a failed one). Warnings (e.g. a file
that changed while being read) are not alerts; they show in the morning summary.

## Channels

ntfy (server, topic, optional token), Discord webhook, Home Assistant webhook,
email (SMTP), and **Unraid notifications** (Unraid's API; needs an API key):
several of each, each with a **Send test** button. Tokens and passwords are
stored encrypted and never shown again or logged.

## Chunks (one pull request each)

| # | Chunk | Contents |
|---|---|---|
| 1 | **Alert engine** | Events, `alerts` table (history, repeat window, recovered), sender with retries in the background, hooks in runner, scheduler, restorer and an hourly check; secrets encrypted |
| 2 | **Channels** | ntfy, Discord, Home Assistant, email, Unraid; tests against fake servers |
| 3 | **Alert settings** | Settings → Alerts: channels (add, edit, test, remove), which events, morning summary time; Dashboard shows recent alerts |
| 4 | **Morning summary** | A daily message at a set time (off by default): last night's result, sizes, warnings, next backup |
| 5 | **Release 0.0.4.0** | Version, notes, tag; owner sets up a channel and tests |
