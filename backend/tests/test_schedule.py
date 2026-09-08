from datetime import datetime
from datetime import timezone as tz
from zoneinfo import ZoneInfo

from app.schedule import is_likely_live_window

ET = ZoneInfo("America/New_York")


def test_true_during_sunday_afternoon():
    assert is_likely_live_window(datetime(2026, 10, 4, 14, 0, tzinfo=ET)) is True


def test_true_during_thursday_night():
    assert is_likely_live_window(datetime(2026, 10, 1, 20, 30, tzinfo=ET)) is True


def test_true_during_monday_night():
    assert is_likely_live_window(datetime(2026, 10, 5, 21, 0, tzinfo=ET)) is True


def test_false_on_tuesday_same_hour_as_monday_night_football():
    assert is_likely_live_window(datetime(2026, 10, 6, 20, 0, tzinfo=ET)) is False


def test_false_after_window_ends_past_midnight():
    """Just after Monday Night Football would have ended -- a new weekday
    (Tuesday), so no window covers it."""
    assert is_likely_live_window(datetime(2026, 10, 6, 0, 30, tzinfo=ET)) is False


def test_false_outside_season():
    """Same weekday and hour as a real Sunday window, but July -- no NFL."""
    assert is_likely_live_window(datetime(2026, 7, 5, 14, 0, tzinfo=ET)) is False


def test_true_during_february_playoffs():
    assert is_likely_live_window(datetime(2026, 2, 1, 18, 0, tzinfo=ET)) is True


def test_converts_from_utc():
    """A UTC timestamp must be converted to ET before the day/hour check --
    comparing UTC's weekday/hour directly would misjudge the window
    whenever it straddles midnight UTC."""
    # Monday 2026-10-05 01:00 UTC == Sunday 2026-10-04 21:00 EDT (UTC-4)
    utc_dt = datetime(2026, 10, 5, 1, 0, tzinfo=tz.utc)
    assert is_likely_live_window(utc_dt) is True


def test_converts_from_utc_during_est():
    """Same check during the EST era (UTC-5, post-DST-fallback) -- the NFL
    season spans both offsets, and a hardcoded UTC-4 assumption would
    misjudge every game after the November DST change."""
    # Tuesday 2027-01-05 01:30 UTC == Monday 2027-01-04 20:30 EST (UTC-5)
    utc_dt = datetime(2027, 1, 5, 1, 30, tzinfo=tz.utc)
    assert is_likely_live_window(utc_dt) is True


# Boundary tests: each hour threshold pinned on both sides, so shifting any
# of them (in either direction) fails a test -- a table of magic numbers is
# only as trustworthy as the tests that would catch it moving.


def test_sunday_window_boundary():
    assert is_likely_live_window(datetime(2026, 10, 4, 12, 59, tzinfo=ET)) is False
    assert is_likely_live_window(datetime(2026, 10, 4, 13, 0, tzinfo=ET)) is True


def test_thursday_window_boundary():
    assert is_likely_live_window(datetime(2026, 10, 1, 19, 59, tzinfo=ET)) is False
    assert is_likely_live_window(datetime(2026, 10, 1, 20, 0, tzinfo=ET)) is True


def test_monday_window_boundary():
    assert is_likely_live_window(datetime(2026, 10, 5, 19, 59, tzinfo=ET)) is False
    assert is_likely_live_window(datetime(2026, 10, 5, 20, 0, tzinfo=ET)) is True


def test_season_boundary_start():
    # Aug 31 and Sep 7 2026 are both Mondays -- an apples-to-apples
    # comparison isolating the month check from the day-of-week check.
    assert is_likely_live_window(datetime(2026, 8, 31, 20, 30, tzinfo=ET)) is False
    assert is_likely_live_window(datetime(2026, 9, 7, 20, 30, tzinfo=ET)) is True


def test_season_boundary_end():
    # Feb 22 and Mar 1 2026 are both Sundays.
    assert is_likely_live_window(datetime(2026, 2, 22, 14, 0, tzinfo=ET)) is True
    assert is_likely_live_window(datetime(2026, 3, 1, 14, 0, tzinfo=ET)) is False


def test_missing_zoneinfo_degrades_to_not_live_rather_than_raising(monkeypatch):
    """sync must never crash the scheduler over a missing tzdata install --
    degrade to 'not live' (the always-safe default) rather than propagating."""
    import app.schedule as schedule_module

    def raise_error(*args, **kwargs):
        raise LookupError("no time zone found with key America/New_York")

    monkeypatch.setattr(schedule_module, "ZoneInfo", raise_error)

    assert is_likely_live_window(datetime(2026, 10, 4, 14, 0, tzinfo=tz.utc)) is False
