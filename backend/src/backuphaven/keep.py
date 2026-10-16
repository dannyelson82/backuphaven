"""Keep rules (ARCHITECTURE.md §6.8): which backups to keep. Pure functions.

Given every backup file BackupHaven made, decide which to keep, and why:

- the newest good backup of each of the last ``nightly`` days, ``weekly`` weeks
  and ``monthly`` months (days, weeks and months in the server's time zone);
- the newest good backup, always;
- for every database, the newest backup holding a healthy copy of it, however
  old: the "last good copy";
- a failed backup until a newer good one exists;
- a backup of chosen apps (Choose apps…) for as many days as nightly backups are
  kept; it never takes a day's, week's or month's place.

"Good" means the backup file was checked after writing and the run didn't fail.
Everything else is deleted.
"""

from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, tzinfo


@dataclass(frozen=True)
class KeepSettings:
    nightly: int = 7
    weekly: int = 4
    monthly: int = 6


@dataclass(frozen=True)
class Backup:
    name: str
    created: datetime  # aware
    good: bool
    chosen: bool = False  # Choose apps…, not the backup schedule
    healthy: frozenset[str] = field(default=frozenset())  # "app/database path"


def _newest_per(backups: list[Backup], key: Callable[[Backup], Hashable], count: int) -> list[str]:
    """The newest backup in each of the ``count`` most recent periods."""
    chosen: list[str] = []
    seen: list[Hashable] = []
    for backup in backups:  # newest first
        period = key(backup)
        if period in seen:
            continue
        if len(seen) == count:
            break
        seen.append(period)
        chosen.append(backup.name)
    return chosen


def decide(
    backups: list[Backup], settings: KeepSettings, tz: tzinfo, now: datetime
) -> dict[str, list[str]]:
    """Every backup's reasons to be kept; an empty list means delete it."""
    newest_first = sorted(backups, key=lambda b: b.created, reverse=True)
    reasons: dict[str, list[str]] = {b.name: [] for b in newest_first}
    good = [b for b in newest_first if b.good and not b.chosen]

    def local(b: Backup) -> datetime:
        return b.created.astimezone(tz)

    for name in _newest_per(good, lambda b: local(b).date(), settings.nightly):
        reasons[name].append("nightly")
    for name in _newest_per(good, lambda b: local(b).isocalendar()[:2], settings.weekly):
        reasons[name].append("weekly")
    for name in _newest_per(good, lambda b: (local(b).year, local(b).month), settings.monthly):
        reasons[name].append("monthly")

    any_good = [b for b in newest_first if b.good]
    if any_good:
        reasons[any_good[0].name].append("newest good backup")

    last_copy: dict[str, str] = {}
    for backup in newest_first:
        for database in backup.healthy:
            last_copy.setdefault(database, backup.name)
    for database, name in sorted(last_copy.items()):
        # Always recorded: it's what stops the backup being deleted by hand.
        reasons[name].append(f"last good copy of {database}")

    newest_good = any_good[0].created if any_good else None
    window = timedelta(days=max(settings.nightly, 1))
    for backup in newest_first:
        if not backup.good and (newest_good is None or backup.created > newest_good):
            reasons[backup.name].append("no newer good backup yet")
        if backup.chosen and backup.good and now - backup.created < window:
            reasons[backup.name].append("chosen apps")
    return reasons
