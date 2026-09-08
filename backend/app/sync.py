import json
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.adapters import espn as espn_adapter
from app.adapters import sleeper as sleeper_adapter
from app.crypto import decrypt_value
from app.db import get_sessionmaker
from app.models import AppSetting, League, Secret, SyncLog, Team
from app.schedule import is_likely_live_window

logger = logging.getLogger(__name__)

# Guards sync_all_platforms against running twice concurrently. Once the
# live-window job (every 60s) and the baseline job (every 20 minutes) are
# both registered, their fixed intervals land on the exact same instant
# every 20 minutes (1200s is a multiple of 60s) -- without this, that's two
# threads writing to the same SQLite league rows at once, which SQLite
# resolves by raising "database is locked" on the loser, surfacing as a
# false degraded-platform banner during the very games this feature serves.
# A non-blocking skip (rather than blocking until the lock is free) also
# means an in-flight sync is never made to run twice back-to-back.
_sync_lock = threading.Lock()


def _get_setting(db: Session, key: str) -> str | None:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else None


def _get_secret(db: Session, key: str) -> str | None:
    row = db.query(Secret).filter(Secret.key == key).first()
    return row.encrypted_value if row else None


def _parse_league_ids(raw: str) -> list[str]:
    """Split the stored comma-joined league IDs, tolerating whitespace around
    separators ("123, 456") which would otherwise produce a leading space in
    the Sleeper URL path."""
    return [x.strip() for x in raw.split(",") if x.strip()]


def _safe_rollback(db: Session) -> None:
    """Roll back without ever propagating. A rollback can itself fail (SQLite
    lock contention between the scheduler thread and a request thread), and
    that must not escape sync_sleeper -- the inline startup call would abort
    application startup."""
    try:
        db.rollback()
    except Exception:
        logger.exception("sleeper sync: rollback failed")


def _sync_one_league(db: Session, league_id: str, my_user_id: str, players_map: dict) -> None:
    """Refresh a single league. Caller commits/rolls back so that one league's
    failure cannot discard another league's work."""
    normalized = sleeper_adapter.normalize_league(league_id, my_user_id, players_map)

    league = (
        db.query(League)
        .filter(League.platform == "sleeper", League.platform_league_id == league_id)
        .first()
    )
    if league is None:
        league = League(
            platform="sleeper",
            platform_league_id=league_id,
            name=normalized["name"],
            season=normalized["season"],
        )
        db.add(league)
        db.flush()
    else:
        league.name = normalized["name"]
        league.season = normalized["season"]
        db.query(Team).filter(Team.league_id == league.id).delete()

    for team_data in normalized["teams"]:
        roster_players = team_data.pop("roster_json")
        db.add(
            Team(
                league_id=league.id,
                roster_json=json.dumps(roster_players),
                **team_data,
            )
        )


def sync_sleeper(db: Session) -> None:
    """Sleeper setup is genuinely optional -- ESPN-only users are now a real,
    supported scenario. Like sync_espn, this checks configuration *before*
    creating any SyncLog row: if unconfigured, /sync-status should simply
    omit "sleeper" from its platform list rather than report a
    permanently-degraded platform."""
    username = _get_setting(db, "sleeper_username")
    league_ids = _parse_league_ids(_get_setting(db, "sleeper_league_ids") or "")
    if not username or not league_ids:
        return

    log = SyncLog(platform="sleeper", started_at=datetime.now(timezone.utc))
    db.add(log)
    db.commit()

    try:
        my_user_id = sleeper_adapter.get_user_id(username)
        players_map = sleeper_adapter.get_players_map()

        # Each league is committed independently: one bad league ID (deleted
        # league, typo, archived at season rollover) must not stop every other
        # league from refreshing.
        errors: list[str] = []
        for league_id in league_ids:
            try:
                _sync_one_league(db, league_id, my_user_id, players_map)
                db.commit()
            except Exception as exc:
                _safe_rollback(db)
                logger.exception("sleeper sync: league %s failed", league_id)
                errors.append(f"league {league_id}: {exc}")

        # Partial success is still a failed sync overall, but the leagues that
        # did work are already committed above.
        log.success = not errors
        log.error = "; ".join(errors) if errors else None
    except Exception as exc:  # sync must never crash the scheduler
        _safe_rollback(db)
        log.success = False
        log.error = str(exc)
    finally:
        # Cleanup is itself guarded: a secondary DB failure while recording the
        # SyncLog must not propagate out of sync_sleeper.
        try:
            log.finished_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            logger.exception("sleeper sync: failed to record SyncLog completion")
            _safe_rollback(db)


def _current_espn_season(now: datetime | None = None) -> int:
    """ESPN league IDs persist across seasons; season is a separate URL
    parameter the adapter needs. NFL seasons span roughly September-February,
    so a January/February sync should still target the season that started
    the previous calendar year."""
    now = now or datetime.now(timezone.utc)
    return now.year - 1 if now.month < 3 else now.year


def _sync_one_espn_league(
    db: Session, league_id: str, season: int, espn_s2: str, swid: str
) -> None:
    """Refresh a single ESPN league. Caller commits/rolls back so that one
    league's failure cannot discard another league's work."""
    normalized = espn_adapter.normalize_league(league_id, season, espn_s2, swid)

    league = (
        db.query(League)
        .filter(League.platform == "espn", League.platform_league_id == league_id)
        .first()
    )
    if league is None:
        league = League(
            platform="espn",
            platform_league_id=league_id,
            name=normalized["name"],
            season=normalized["season"],
        )
        db.add(league)
        db.flush()
    else:
        league.name = normalized["name"]
        league.season = normalized["season"]
        db.query(Team).filter(Team.league_id == league.id).delete()

    for team_data in normalized["teams"]:
        roster_players = team_data.pop("roster_json")
        db.add(
            Team(
                league_id=league.id,
                roster_json=json.dumps(roster_players),
                **team_data,
            )
        )


def sync_espn(db: Session) -> None:
    """ESPN setup is genuinely optional -- many users may never configure it.
    Unlike sync_sleeper, this checks configuration *before* creating any
    SyncLog row: if unconfigured, /sync-status should simply omit "espn" from
    its platform list rather than report a permanently-degraded platform."""
    league_ids = _parse_league_ids(_get_setting(db, "espn_league_ids") or "")
    espn_s2_encrypted = _get_secret(db, "espn_s2")
    swid_encrypted = _get_secret(db, "espn_swid")
    if not league_ids or not espn_s2_encrypted or not swid_encrypted:
        return

    log = SyncLog(platform="espn", started_at=datetime.now(timezone.utc))
    db.add(log)
    db.commit()

    try:
        espn_s2 = decrypt_value(espn_s2_encrypted)
        swid = decrypt_value(swid_encrypted)
        season = _current_espn_season()

        errors: list[str] = []
        for league_id in league_ids:
            try:
                _sync_one_espn_league(db, league_id, season, espn_s2, swid)
                db.commit()
            except Exception as exc:
                _safe_rollback(db)
                logger.exception("espn sync: league %s failed", league_id)
                errors.append(f"league {league_id}: {exc}")

        log.success = not errors
        log.error = "; ".join(errors) if errors else None
    except Exception as exc:  # sync must never crash the scheduler
        _safe_rollback(db)
        log.success = False
        log.error = str(exc)
    finally:
        try:
            log.finished_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            logger.exception("espn sync: failed to record SyncLog completion")
            _safe_rollback(db)


def sync_all_platforms() -> None:
    if not _sync_lock.acquire(blocking=False):
        logger.info("sync_all_platforms: a sync is already in progress, skipping this trigger")
        return
    try:
        db = get_sessionmaker()()
        try:
            sync_sleeper(db)
            sync_espn(db)
            # Yahoo adapter is added by its own follow-on plan.
        finally:
            db.close()
    finally:
        _sync_lock.release()


def sync_all_platforms_during_live_window() -> None:
    """Registered as a second, frequent (60s) scheduler job alongside the
    existing 20-minute sync_all_platforms job -- outside a likely-live
    window this is a no-op, so it adds no load the rest of the time. The
    20-minute job and the manual "Sync now" button are unaffected by this
    and keep running unconditionally (sync_all_platforms's own lock is what
    keeps the two jobs from colliding when their schedules coincide)."""
    if is_likely_live_window():
        sync_all_platforms()
