from app.adapters import sleeper as sleeper_adapter
from app.db import get_sessionmaker, init_db
from app.models import AppSetting, League, Team
from app.sync import sync_sleeper


def test_sync_sleeper_creates_league_and_teams(monkeypatch):
    init_db()
    db = get_sessionmaker()()
    db.add(AppSetting(key="sleeper_username", value="myuser"))
    db.add(AppSetting(key="sleeper_league_ids", value="999"))
    db.commit()

    monkeypatch.setattr(sleeper_adapter, "get_user_id", lambda username: "u1")
    monkeypatch.setattr(sleeper_adapter, "get_players_map", lambda: {})
    monkeypatch.setattr(
        sleeper_adapter,
        "normalize_league",
        lambda league_id, my_user_id, players_map: {
            "platform": "sleeper",
            "platform_league_id": league_id,
            "name": "Test League",
            "season": "2026",
            "teams": [
                {
                    "platform_team_id": "1",
                    "name": "Me",
                    "is_mine": True,
                    "roster_json": [],
                    "points_for": 0.0,
                    "opponent_name": None,
                    "opponent_points": None,
                    "week": 1,
                }
            ],
        },
    )

    sync_sleeper(db)

    league = db.query(League).filter(League.platform_league_id == "999").first()
    assert league is not None
    assert league.name == "Test League"
    teams = db.query(Team).filter(Team.league_id == league.id).all()
    assert len(teams) == 1
    assert teams[0].name == "Me"
    db.close()


def test_sync_sleeper_swallows_adapter_http_errors(monkeypatch):
    """The adapter raises both SleeperAdapterError and httpx.HTTPStatusError
    (from raise_for_status). Neither may escape and kill the scheduler."""
    import httpx

    from app.models import SyncLog

    init_db()
    db = get_sessionmaker()()
    db.add(AppSetting(key="sleeper_username", value="myuser"))
    db.add(AppSetting(key="sleeper_league_ids", value="999"))
    db.commit()

    def raise_http_status_error():
        request = httpx.Request("GET", "https://api.sleeper.app/v1/players/nfl")
        response = httpx.Response(500, request=request)
        response.raise_for_status()

    monkeypatch.setattr(sleeper_adapter, "get_user_id", lambda username: "u1")
    monkeypatch.setattr(sleeper_adapter, "get_players_map", raise_http_status_error)

    sync_sleeper(db)  # must not raise

    log = db.query(SyncLog).filter(SyncLog.platform == "sleeper").first()
    assert log is not None
    assert log.success is False
    assert "500" in log.error
    assert db.query(League).count() == 0
    db.close()


def test_sync_sleeper_records_failure_without_raising(monkeypatch):
    init_db()
    db = get_sessionmaker()()
    # No AppSetting rows configured — sync_sleeper should record a failed
    # SyncLog, not raise, so the scheduler never crashes.
    from app.models import SyncLog

    sync_sleeper(db)

    log = db.query(SyncLog).filter(SyncLog.platform == "sleeper").first()
    assert log is not None
    assert log.success is False
    assert log.error is not None
    db.close()
