import json
from datetime import timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.db import get_db
from app.models import League, SyncLog, Team

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
