---
title: Alerts
order: 45
summary: Get told when something goes wrong, and when it's fixed.
---

# Alerts

BackupHaven tells you when something needs your attention:

| Alert | When |
|---|---|
| **Backup failed** | A backup ended **Failed**: an app couldn't be backed up, the backup file failed its check, or the disk filled up |
| **Database damaged** | A database check found damage. Look at the app soon; older backups may hold a healthy copy |
| **Backup missed** | A scheduled backup didn't run, because the server was off or another backup was still running |
| **No good backup** | No good backup for more than 36 hours |
| **Disk nearly full** | The backups disk has less than twice the **Minimum free space** left |
| **Restore failed** | A restore didn't finish (your files were put back) |
| **App not protected** | An app in the backup schedule has no plugin any more |
| **Container didn't start** | An app stopped for its backup didn't start again. Start it in Unraid |

When the problem is fixed you get a **Recovered** message. The same alert isn't
sent again for 12 hours while it's still a problem. Open alerts also show at the
top of the Dashboard.

## Channels

Add one or more in **Settings → Alerts → Add a channel**:

- **ntfy**: free phone notifications. Install the ntfy app, subscribe to a
  topic with a name that's hard to guess, and enter the same topic here. You can
  also use your own ntfy server, and an access token for a protected topic.
- **Discord**: a channel webhook (Edit Channel → Integrations → Webhooks → Copy
  Webhook URL).
- **Home Assistant**: an automation with a **Webhook** trigger. Paste its
  address (`http://<home assistant>:8123/api/webhook/<id>`). The automation
  receives `title`, `message` and `state` (`open`, `recovered` or `info`).
- **Email**: your mail provider's SMTP server, port and login.
- **Unraid notifications**: alerts appear in Unraid's notification bell and go
  wherever Unraid sends its notifications. Create an API key in Unraid (Settings →
  Management Access → API Keys) with access to notifications, and enter
  `http://<your server>/graphql`.

Tokens, passwords and webhook addresses are stored encrypted and never shown
again. When editing a channel, leave that field empty to keep the saved one.

Press **Send test** to check a channel. Under **Send these alerts**, choose
which alerts each channel gets, for example everything to your phone and only
**Database damaged** by email.

## Morning summary

Switch on **Morning summary** and choose a time, to get a daily message about
last night's backup on the channels that have **Morning summary** ticked.
