import json

from app.db import get_sessionmaker, init_db
from app.models import League, Team

TEST_PASSWORD = "testpassword123"


def _seed_my_team(roster, league_name="Test League"):
    init_db()
    db = get_sessionmaker()()
    league = League(platform="sleeper", platform_league_id="1", name=league_name, season="2026")
    db.add(league)
    db.flush()
    db.add(
        Team(
            league_id=league.id,
            platform_team_id="1",
            name="Me",
            is_mine=True,
            roster_json=json.dumps(roster),
            points_for=0.0,
        )
    )
    db.commit()
    db.close()


def test_nfl_scores_requires_auth(client):
    resp = client.get("/nfl-scores")
    assert resp.status_code == 401


def test_nfl_scores_returns_only_games_with_my_players(client, monkeypatch):
    from app.routers import live_scores as live_scores_router

    _seed_my_team(
        [{"player_id": "p1", "name": "Bo Nix", "position": "QB", "team": "DEN", "is_starter": True}]
    )
    client.post("/auth/login", json={"password": TEST_PASSWORD})

    monkeypatch.setattr(
        live_scores_router,
        "get_live_games",
        lambda relevant_teams: (
            [
                {
                    "home_team": "DEN",
                    "away_team": "LAC",
                    "home_score": 14,
                    "away_score": 10,
                    "state": "in",
                    "detail": "Q2",
                }
            ]
            if "DEN" in relevant_teams
            else []
        ),
    )

    resp = client.get("/nfl-scores")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["home_team"] == "DEN"
    assert any(p["name"] == "Bo Nix" for p in data[0]["home_players"])
    assert data[0]["home_players"][0]["league_name"] == "Test League"
    assert data[0]["away_players"] == []


def test_nfl_scores_returns_empty_list_when_no_teams_owned(client):
    init_db()
    client.post("/auth/login", json={"password": TEST_PASSWORD})
    resp = client.get("/nfl-scores")
    assert resp.status_code == 200
    assert resp.json() == []
