import json

from app.adapters import sleeper as sleeper_adapter
from app.db import get_sessionmaker, init_db
from app.models import League, Team

TEST_PASSWORD = "testpassword123"


def _login(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})


def _make_league(platform="sleeper", platform_league_id="1"):
    init_db()
    db = get_sessionmaker()()
    league = League(
        platform=platform,
        platform_league_id=platform_league_id,
        name=f"Test League {platform_league_id}",
        season="2026",
    )
    db.add(league)
    db.commit()
    league_id = league.id
    db.close()
    return league_id


def _seed_league_with_rosters(rosters, platform_league_id="1", week=None):
    league_id = _make_league(platform_league_id=platform_league_id)
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
                week=week,
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
        {
            "player_id": "p2",
            "name": "Free Agent",
            "position": "WR",
            "team": "SF",
            "trend_count": 50,
            "actual_points": None,
            "projected_points": None,
        }
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
        {
            "player_id": "mystery",
            "name": "mystery",
            "position": None,
            "team": None,
            "trend_count": 1,
            "actual_points": None,
            "projected_points": None,
        }
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


def test_waiver_wire_excludes_players_rostered_on_any_team_in_the_league(client, monkeypatch):
    """"Rostered" is the union across every team in the league, not just mine —
    a player owned by a rival is still unavailable to me."""
    league_id = _seed_league_with_rosters(
        [
            [{"player_id": "mine", "name": "My Guy", "position": "RB", "team": "KC"}],
            [{"player_id": "theirs", "name": "Rival Guy", "position": "WR", "team": "SF"}],
        ]
    )
    _login(client)

    monkeypatch.setattr(
        sleeper_adapter,
        "get_trending_adds",
        lambda: [
            {"player_id": "mine", "count": 300},
            {"player_id": "theirs", "count": 200},
            {"player_id": "free", "count": 100},
        ],
    )
    monkeypatch.setattr(
        sleeper_adapter,
        "get_players_map",
        lambda: {"free": {"full_name": "Free Agent", "position": "TE", "team": "BUF"}},
    )

    resp = client.get(f"/leagues/{league_id}/waiver-wire")
    assert resp.status_code == 200
    # "theirs" is on the *second* team; only the cross-team union excludes it.
    assert [p["player_id"] for p in resp.json()] == ["free"]


def test_waiver_wire_is_computed_per_league(client, monkeypatch):
    """Availability is per-league: a player owned in league A is still a valid
    suggestion in league B, where nobody rosters him."""
    league_a = _seed_league_with_rosters(
        [[{"player_id": "shared", "name": "Owned In A", "position": "QB", "team": "PHI"}]],
        platform_league_id="A",
    )
    league_b = _seed_league_with_rosters([[]], platform_league_id="B")
    _login(client)

    monkeypatch.setattr(
        sleeper_adapter, "get_trending_adds", lambda: [{"player_id": "shared", "count": 42}]
    )
    monkeypatch.setattr(
        sleeper_adapter,
        "get_players_map",
        lambda: {"shared": {"full_name": "Shared Guy", "position": "QB", "team": "PHI"}},
    )

    assert client.get(f"/leagues/{league_a}/waiver-wire").json() == []
    assert client.get(f"/leagues/{league_b}/waiver-wire").json() == [
        {
            "player_id": "shared",
            "name": "Shared Guy",
            "position": "QB",
            "team": "PHI",
            "trend_count": 42,
            "actual_points": None,
            "projected_points": None,
        }
    ]


def test_waiver_wire_degrades_to_empty_list_on_malformed_trending_payload(client, monkeypatch):
    """Sleeper returning a shape we don't expect (here an error dict, then an
    entry missing "count") must degrade to [], not 500."""
    league_id = _seed_league_with_rosters([[]])
    _login(client)
    monkeypatch.setattr(sleeper_adapter, "get_players_map", lambda: {})

    monkeypatch.setattr(sleeper_adapter, "get_trending_adds", lambda: {"error": "rate limited"})
    resp = client.get(f"/leagues/{league_id}/waiver-wire")
    assert resp.status_code == 200
    assert resp.json() == []

    monkeypatch.setattr(sleeper_adapter, "get_trending_adds", lambda: [{"player_id": "p1"}])
    resp = client.get(f"/leagues/{league_id}/waiver-wire")
    assert resp.status_code == 200
    assert resp.json() == []


def test_waiver_wire_includes_points_once_a_week_is_known(client, monkeypatch):
    """Once at least one team has synced (so a current week is known),
    trending players get actual/projected points too."""
    league_id = _seed_league_with_rosters([[]], week=5)
    _login(client)

    monkeypatch.setattr(
        sleeper_adapter, "get_trending_adds", lambda: [{"player_id": "p1", "count": 10}]
    )
    monkeypatch.setattr(
        sleeper_adapter,
        "get_players_map",
        lambda: {"p1": {"full_name": "Hot Pickup", "position": "WR", "team": "MIA"}},
    )
    monkeypatch.setattr(
        sleeper_adapter,
        "get_weekly_points",
        lambda league_id, season, week: ({"p1": 12.5}, {"p1": 9.0}),
    )

    resp = client.get(f"/leagues/{league_id}/waiver-wire")
    assert resp.status_code == 200
    assert resp.json() == [
        {
            "player_id": "p1",
            "name": "Hot Pickup",
            "position": "WR",
            "team": "MIA",
            "trend_count": 10,
            "actual_points": 12.5,
            "projected_points": 9.0,
        }
    ]


def test_waiver_wire_degrades_to_no_points_when_weekly_points_lookup_fails(client, monkeypatch):
    """A hiccup on the points lookup shouldn't take down the whole waiver
    wire -- trending suggestions still show, just without point totals."""
    league_id = _seed_league_with_rosters([[]], week=5)
    _login(client)

    monkeypatch.setattr(
        sleeper_adapter, "get_trending_adds", lambda: [{"player_id": "p1", "count": 10}]
    )
    monkeypatch.setattr(
        sleeper_adapter,
        "get_players_map",
        lambda: {"p1": {"full_name": "Hot Pickup", "position": "WR", "team": "MIA"}},
    )

    def raise_error(league_id, season, week):
        raise RuntimeError("undocumented host is down")

    monkeypatch.setattr(sleeper_adapter, "get_weekly_points", raise_error)

    resp = client.get(f"/leagues/{league_id}/waiver-wire")
    assert resp.status_code == 200
    assert resp.json() == [
        {
            "player_id": "p1",
            "name": "Hot Pickup",
            "position": "WR",
            "team": "MIA",
            "trend_count": 10,
            "actual_points": None,
            "projected_points": None,
        }
    ]
