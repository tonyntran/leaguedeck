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


def test_isolation_probe_a():
    """Paired with test_isolation_probe_b to prove per-test DB isolation.

    Each test creates a league with the same platform_league_id in what should
    be its own throwaway sqlite file (a fresh tmp_path per test, per the
    autouse `test_env` fixture). If `get_settings`/`get_engine` caching ever
    leaks across tests again, this pair starts failing because one test would
    see the other's row.
    """
    init_db()
    db = get_sessionmaker()()
    assert db.query(League).filter(League.platform_league_id == "iso-1").count() == 0
    db.add(League(platform="sleeper", platform_league_id="iso-1", name="Iso A", season="2026"))
    db.commit()
    assert db.query(League).filter(League.platform_league_id == "iso-1").count() == 1
    db.close()


def test_isolation_probe_b():
    init_db()
    db = get_sessionmaker()()
    assert db.query(League).filter(League.platform_league_id == "iso-1").count() == 0
    db.add(League(platform="sleeper", platform_league_id="iso-1", name="Iso B", season="2026"))
    db.commit()
    assert db.query(League).filter(League.platform_league_id == "iso-1").count() == 1
    db.close()
