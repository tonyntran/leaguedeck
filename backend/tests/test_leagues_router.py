import json

from app.db import get_sessionmaker, init_db
from app.models import League, Team

TEST_PASSWORD = "testpassword123"


def _seed_league():
    init_db()
    db = get_sessionmaker()()
    league = League(platform="sleeper", platform_league_id="999", name="Test League", season="2026")
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
    db.close()


def test_list_leagues_requires_auth(client):
    resp = client.get("/leagues")
    assert resp.status_code == 401


def test_list_leagues_returns_seeded_data(client):
    _seed_league()
    client.post("/auth/login", json={"password": TEST_PASSWORD})

    resp = client.get("/leagues")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Test League"
    assert data[0]["platform_league_id"] == "999"
    assert data[0]["teams"][0]["roster"][0]["name"] == "Player One"
    assert data[0]["teams"][0]["opponent_name"] == "Rival"
