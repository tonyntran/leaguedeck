import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.db import get_db
from app.live_scores import get_live_games
from app.models import League, Team

router = APIRouter(prefix="/nfl-scores", tags=["nfl-scores"], dependencies=[Depends(require_auth)])


@router.get("")
def get_nfl_scores(db: Session = Depends(get_db)):
    my_teams = db.query(Team).filter(Team.is_mine == True).all()  # noqa: E712
    league_ids = {t.league_id for t in my_teams}
    leagues_by_id = {
        league.id: league for league in db.query(League).filter(League.id.in_(league_ids)).all()
    }

    players_by_pro_team: dict[str, list[dict]] = {}
    relevant_teams: set[str] = set()
    for team in my_teams:
        league = leagues_by_id.get(team.league_id)
        league_name = league.name if league else "Unknown league"
        for player in json.loads(team.roster_json):
            pro_team = player.get("team")
            if not pro_team:
                continue
            relevant_teams.add(pro_team)
            players_by_pro_team.setdefault(pro_team, []).append(
                {
                    "name": player["name"],
                    "position": player.get("position"),
                    "league_name": league_name,
                }
            )

    games = get_live_games(relevant_teams)
    for game in games:
        game["home_players"] = players_by_pro_team.get(game["home_team"], [])
        game["away_players"] = players_by_pro_team.get(game["away_team"], [])
    return games
