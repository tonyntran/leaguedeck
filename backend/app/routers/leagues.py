import json
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.adapters import sleeper
from app.auth import require_auth
from app.db import get_db
from app.models import League, SyncLog, Team
from app.sync import sync_all_platforms

router = APIRouter(prefix="/leagues", tags=["leagues"], dependencies=[Depends(require_auth)])


@router.get("")
def list_leagues(db: Session = Depends(get_db)):
    leagues = db.query(League).all()
    result = []
    for league in leagues:
        teams = db.query(Team).filter(Team.league_id == league.id).all()
        result.append(
            {
                "id": league.id,
                "platform": league.platform,
                "name": league.name,
                "season": league.season,
                "teams": [
                    {
                        "id": t.id,
                        "name": t.name,
                        "is_mine": t.is_mine,
                        "roster": json.loads(t.roster_json),
                        "points_for": t.points_for,
                        "opponent_name": t.opponent_name,
                        "opponent_points": t.opponent_points,
                        "week": t.week,
                    }
                    for t in teams
                ],
            }
        )
    return result


@router.get("/{league_id}/waiver-wire")
def get_waiver_wire(league_id: int, db: Session = Depends(get_db)):
    league = db.query(League).filter(League.id == league_id).first()
    if league is None or league.platform != "sleeper":
        raise HTTPException(status_code=404, detail="League not found")

    teams = db.query(Team).filter(Team.league_id == league.id).all()
    if not teams:
        return []

    rostered_ids = {
        player["player_id"] for team in teams for player in json.loads(team.roster_json)
    }

    try:
        trending = sleeper.get_trending_adds()
        players_map = sleeper.get_players_map()
    except Exception:
        # A Sleeper hiccup on this nice-to-have feature shouldn't read as a
        # broken dashboard — degrade to no suggestions rather than a 500.
        return []

    available = [
        {
            "player_id": t["player_id"],
            "name": players_map.get(t["player_id"], {}).get("full_name", t["player_id"]),
            "position": players_map.get(t["player_id"], {}).get("position"),
            "team": players_map.get(t["player_id"], {}).get("team"),
            "trend_count": t["count"],
        }
        for t in trending
        if t["player_id"] not in rostered_ids
    ]
    return sorted(available, key=lambda p: p["trend_count"], reverse=True)


sync_status_router = APIRouter(
    prefix="/sync-status", tags=["sync"], dependencies=[Depends(require_auth)]
)


@sync_status_router.get("")
def get_sync_status(db: Session = Depends(get_db)):
    platforms = [row[0] for row in db.query(SyncLog.platform).distinct().all()]
    result = []
    for platform in platforms:
        latest = (
            db.query(SyncLog)
            .filter(SyncLog.platform == platform)
            .order_by(SyncLog.started_at.desc())
            .first()
        )
        result.append(
            {
                "platform": platform,
                # SQLite's DateTime column drops tzinfo on round-trip. Every
                # timestamp written here is datetime.now(timezone.utc), so
                # re-attaching UTC is unambiguous — and necessary, or the ISO
                # string carries no offset and JavaScript's new Date() parses
                # it as local time.
                "last_success_at": (
                    latest.finished_at.replace(tzinfo=timezone.utc).isoformat()
                    if latest.finished_at
                    else None
                ),
                "last_success": latest.success,
                "last_error": latest.error,
            }
        )
    return result


@sync_status_router.post("/run")
def trigger_sync():
    """Runs synchronously, so it can take up to ~30s on a cold players cache
    (see the sync-blocking-lifespan note in the design spec) — callers should
    show a loading state while this is in flight."""
    sync_all_platforms()
    return {"ok": True}
