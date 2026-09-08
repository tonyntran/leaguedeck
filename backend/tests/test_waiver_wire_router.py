import json

from app.adapters import sleeper as sleeper_adapter
from app.db import get_sessionmaker, init_db
from app.models import League, Team

TEST_PASSWORD = "testpassword123"


def _login(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})


def _make_league(platform="sleeper"):
    init_db()
    db = get_sessionmaker()()
    league = League(platform=platform, platform_league_id="1", name="Test League", season="2026")
    db.add(league)
    db.commit()
    league_id = league.id
    db.close()
    return league_id


def _seed_league_with_rosters(rosters):
    league_id = _make_league()
    db = get_sessionmaker()()
    for i, roster in enumerate(rosters):
        db.add(
            Team(
                league_id=league_id,
                platform_team_id=str(i),
                name=f"Team {i}",
                is_mine=(i == 0),
                roster_json=json.dumps(roster),
                points_for=0.0,
            )
        )
    db.commit()
    db.close()
    return league_id


def test_waiver_wire_requires_auth(client):
    resp = client.get("/leagues/1/waiver-wire")
    assert resp.status_code == 401


def test_waiver_wire_returns_404_for_missing_league(client):
    _login(client)
    resp = client.get("/leagues/999/waiver-wire")
    assert resp.status_code == 404


def test_waiver_wire_404s_for_non_sleeper_platform(client):
    league_id = _make_league(platform="espn")
    _login(client)
    resp = client.get(f"/leagues/{league_id}/waiver-wire")
    assert resp.status_code == 404


def test_waiver_wire_returns_empty_list_for_league_with_no_teams(client):
    league_id = _make_league()
    _login(client)
    resp = client.get(f"/leagues/{league_id}/waiver-wire")
    assert resp.status_code == 200
    assert resp.json() == []


def test_waiver_wire_excludes_already_rostered_players(client, monkeypatch):
    league_id = _seed_league_with_rosters(
        [[{"player_id": "p1", "name": "Rostered Guy", "position": "RB", "team": "KC", "is_starter": True}]]
    )
    _login(client)

    monkeypatch.setattr(
        sleeper_adapter,
        "get_trending_adds",
        lambda: [{"player_id": "p1", "count": 100}, {"player_id": "p2", "count": 50}],
    )
    monkeypatch.setattr(
        sleeper_adapter,
        "get_players_map",
        lambda: {"p2": {"full_name": "Free Agent", "position": "WR", "team": "SF"}},
    )

    resp = client.get(f"/leagues/{league_id}/waiver-wire")
    assert resp.status_code == 200
    assert resp.json() == [
        {"player_id": "p2", "name": "Free Agent", "position": "WR", "team": "SF", "trend_count": 50}
    ]


def test_waiver_wire_sorts_by_trend_count_descending(client, monkeypatch):
    league_id = _seed_league_with_rosters([[]])
    _login(client)

    monkeypatch.setattr(
        sleeper_adapter,
        "get_trending_adds",
        lambda: [{"player_id": "low", "count": 5}, {"player_id": "high", "count": 500}],
    )
    monkeypatch.setattr(sleeper_adapter, "get_players_map", lambda: {})

    resp = client.get(f"/leagues/{league_id}/waiver-wire")
    assert [p["player_id"] for p in resp.json()] == ["high", "low"]


def test_waiver_wire_falls_back_to_raw_id_for_unknown_player(client, monkeypatch):
    league_id = _seed_league_with_rosters([[]])
    _login(client)

    monkeypatch.setattr(
        sleeper_adapter, "get_trending_adds", lambda: [{"player_id": "mystery", "count": 1}]
    )
    monkeypatch.setattr(sleeper_adapter, "get_players_map", lambda: {})

    resp = client.get(f"/leagues/{league_id}/waiver-wire")
    assert resp.json() == [
        {"player_id": "mystery", "name": "mystery", "position": None, "team": None, "trend_count": 1}
    ]


def test_waiver_wire_degrades_to_empty_list_when_sleeper_fails(client, monkeypatch):
    league_id = _seed_league_with_rosters([[]])
    _login(client)

    def raise_error():
        raise sleeper_adapter.SleeperAdapterError("boom")

    monkeypatch.setattr(sleeper_adapter, "get_trending_adds", raise_error)

    resp = client.get(f"/leagues/{league_id}/waiver-wire")
    assert resp.status_code == 200
    assert resp.json() == []
