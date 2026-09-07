import json

from app.db import get_sessionmaker, init_db
from app.models import League, Team


def test_can_create_league_with_team_and_query_it_back():
    init_db()
    db = get_sessionmaker()()

    league = League(
        platform="sleeper",
        platform_league_id="999",
        name="Test League",
        season="2026",
    )
    db.add(league)
    db.flush()

    db.add(
        Team(
            league_id=league.id,
            platform_team_id="1",
            name="Me",
            is_mine=True,
            roster_json=json.dumps([{"player_id": "p1", "name": "Player One"}]),
            points_for=100.5,
            opponent_name="Rival",
            opponent_points=90.2,
            week=1,
        )
    )
    db.commit()

    fetched = db.query(League).filter(League.platform_league_id == "999").first()
    assert fetched.name == "Test League"
    teams = db.query(Team).filter(Team.league_id == fetched.id).all()
    assert len(teams) == 1
    assert teams[0].name == "Me"
    assert json.loads(teams[0].roster_json)[0]["name"] == "Player One"
    db.close()
