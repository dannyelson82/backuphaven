"""Keep rules (ARCHITECTURE.md §6.8): pure decisions, many cases."""

from datetime import UTC, datetime, timedelta

from backuphaven.keep import Backup, KeepSettings, decide

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def nightly(days: int, start: datetime = NOW, **extra) -> list[Backup]:  # type: ignore[no-untyped-def]
    """One good backup at 03:00 on each of the last ``days`` days."""
    first = start.replace(hour=3, minute=0)
    return [
        Backup(name=f"b{i:03d}", created=first - timedelta(days=i), good=True, **extra)
        for i in range(days)
    ]


def kept(reasons: dict[str, list[str]]) -> list[str]:
    return sorted(name for name, why in reasons.items() if why)


def test_a_year_of_nightly_backups_keeps_seven_nights_four_weeks_six_months() -> None:
    backups = nightly(365)
    reasons = decide(backups, KeepSettings(), UTC, NOW)
    names = kept(reasons)
    # 7 nights; weeks and months overlap with them and each other.
    assert all(f"b{i:03d}" in names for i in range(7))
    assert 7 <= len(names) <= 7 + 4 + 6
    weekly = [n for n, why in reasons.items() if "weekly" in why]
    monthly = [n for n, why in reasons.items() if "monthly" in why]
    assert len(weekly) == 4 and len(monthly) == 6
    oldest = min(b.created for b in backups if b.name in names)
    assert NOW - oldest < timedelta(days=6 * 31 + 1)  # nothing older than ~6 months


def test_the_newest_good_backup_is_always_kept_even_with_zero_counts() -> None:
    backups = nightly(3)
    reasons = decide(backups, KeepSettings(nightly=0, weekly=0, monthly=0), UTC, NOW)
    assert kept(reasons) == ["b000"]
    assert reasons["b000"] == ["newest good backup"]


def test_the_last_good_copy_of_a_database_is_kept_however_old() -> None:
    backups = nightly(60)
    # Plex's database was damaged in every backup of the last 50 days.
    healthy_plex = {f"b{i:03d}" for i in range(50, 60)}
    backups = [
        Backup(
            b.name,
            b.created,
            b.good,
            healthy=frozenset({"plex/library.db"} if b.name in healthy_plex else set()),
        )
        for b in backups
    ]
    reasons = decide(backups, KeepSettings(nightly=7, weekly=0, monthly=0), UTC, NOW)
    assert reasons["b050"] == ["last good copy of plex/library.db"]
    assert reasons["b051"] == []


def test_failed_backups_go_once_a_newer_good_one_exists() -> None:
    good = nightly(3)
    failed_new = Backup("failed-new", NOW - timedelta(hours=1), good=False)
    failed_old = Backup("failed-old", NOW - timedelta(days=1, hours=1), good=False)
    reasons = decide([*good, failed_new, failed_old], KeepSettings(), UTC, NOW)
    assert reasons["failed-new"] == ["no newer good backup yet"]
    assert reasons["failed-old"] == []


def test_when_everything_failed_nothing_is_deleted() -> None:
    backups = [Backup(f"f{i}", NOW - timedelta(days=i), good=False) for i in range(10)]
    reasons = decide(backups, KeepSettings(), UTC, NOW)
    assert kept(reasons) == sorted(b.name for b in backups)


def test_chosen_apps_backups_never_take_a_nightly_place() -> None:
    scheduled = nightly(10)
    # A test backup of one app at noon yesterday, after that night's full one.
    chosen = Backup("chosen", NOW - timedelta(days=1), good=True, chosen=True)
    reasons = decide([*scheduled, chosen], KeepSettings(nightly=7, weekly=0, monthly=0), UTC, NOW)
    assert reasons["b001"][0] == "nightly"  # the full backup that night stays
    assert reasons["chosen"] == ["chosen apps"]
    old_chosen = Backup("old-chosen", NOW - timedelta(days=9), good=True, chosen=True)
    reasons = decide(
        [*scheduled, old_chosen], KeepSettings(nightly=7, weekly=0, monthly=0), UTC, NOW
    )
    assert reasons["old-chosen"] == []


def test_several_backups_a_day_keep_the_newest_of_each_day() -> None:
    day = NOW.replace(hour=0)
    backups = [Backup(f"h{h:02d}", day + timedelta(hours=h), good=True) for h in (3, 9, 11)]
    reasons = decide(backups, KeepSettings(nightly=7, weekly=0, monthly=0), UTC, NOW)
    assert kept(reasons) == ["h11"]


def test_days_follow_the_server_time_zone() -> None:
    from backuphaven.timetable import zone

    sydney = zone("Australia/Sydney")  # UTC+11 in October
    # 14:00 and 23:00 UTC on the 15th are the 16th 01:00 and 10:00 in Sydney.
    a = Backup("a", datetime(2026, 10, 15, 14, 0, tzinfo=UTC), good=True)
    b = Backup("b", datetime(2026, 10, 15, 23, 0, tzinfo=UTC), good=True)
    in_utc = decide([a, b], KeepSettings(nightly=7, weekly=0, monthly=0), UTC, NOW)
    in_sydney = decide([a, b], KeepSettings(nightly=7, weekly=0, monthly=0), sydney, NOW)
    assert kept(in_utc) == ["b"] and kept(in_sydney) == ["b"]  # same day either way
    c = Backup("c", datetime(2026, 10, 15, 12, 0, tzinfo=UTC), good=True)  # 15th 23:00 Sydney
    in_sydney = decide([a, b, c], KeepSettings(nightly=7, weekly=0, monthly=0), sydney, NOW)
    assert kept(in_sydney) == ["b", "c"]


def test_no_backups() -> None:
    assert decide([], KeepSettings(), UTC, NOW) == {}
