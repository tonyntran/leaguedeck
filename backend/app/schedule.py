import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

NFL_SEASON_MONTHS = {9, 10, 11, 12, 1, 2}  # regular season through the Super Bowl


def is_likely_live_window(now: datetime | None = None) -> bool:
    """A coarse, best-effort approximation of "an NFL game is probably in
    progress right now" -- hardcoded typical broadcast windows (Thursday
    Night Football, the Sunday slate, Monday Night Football), not real
    per-game kickoff times fetched from either platform. This misses flexed
    Saturday games in December, mid-week schedule changes, bye-week
    nuances, and games running past midnight ET -- an accepted
    approximation, since a false positive only costs one extra sync cycle
    (or one extra cache miss) and a false negative just falls back to the
    existing slower baseline.

    Used by both sync.py (gates the fast scheduler job) and the Sleeper
    adapter (gates the actual-stats cache TTL) -- a shared, dependency-free
    module avoids either one importing from the other.
    """
    try:
        now = now or datetime.now(timezone.utc)
        et = now.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        # A missing tzdata install (e.g. a minimal base image) must not
        # crash whichever caller invoked this -- fall back to "not live",
        # which just means callers use their slower, always-safe default.
        logger.exception("schedule: timezone conversion failed, assuming not live")
        return False
    if et.month not in NFL_SEASON_MONTHS:
        return False
    weekday, hour = et.weekday(), et.hour
    if weekday == 3:  # Thursday
        return hour >= 20
    if weekday == 6:  # Sunday
        return hour >= 13
    if weekday == 0:  # Monday
        return hour >= 20
    return False
