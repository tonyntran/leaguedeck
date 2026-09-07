import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.adapters import sleeper as sleeper_adapter
from app.db import get_sessionmaker
from app.models import AppSetting, League, SyncLog, Team


def _get_setting(db: Session, key: str) -> str | None:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else None


def sync_sleeper(db: Session) -> None:
    log = SyncLog(platform="sleeper", started_at=datetime.now(timezone.utc))
    db.add(log)
    db.commit()

    try:
        username = _get_setting(db, "sleeper_username")
        league_ids_raw = _get_setting(db, "sleeper_league_ids") or ""
        league_ids = [x for x in league_ids_raw.split(",") if x]
        if not username or not league_ids:
            raise ValueError("Sleeper username/league IDs not configured")

        my_user_id = sleeper_adapter.get_user_id(username)
        players_map = sleeper_adapter.get_players_map()

        for league_id in league_ids:
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

        db.commit()
        log.success = True
    except Exception as exc:  # sync must never crash the scheduler
        db.rollback()
        log.success = False
        log.error = str(exc)
    finally:
        log.finished_at = datetime.now(timezone.utc)
        db.commit()


def sync_all_platforms() -> None:
    db = get_sessionmaker()()
    try:
        sync_sleeper(db)
        # ESPN and Yahoo adapters are added by their own follow-on plans.
    finally:
        db.close()
