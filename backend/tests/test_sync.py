from app.adapters import sleeper as sleeper_adapter
from app.db import get_sessionmaker, init_db
from app.models import AppSetting, League, Team
from app.sync import sync_all_platforms_during_live_window, sync_sleeper


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


def _normalized(league_id, name="League", team_name="Me"):
    return {
        "platform": "sleeper",
        "platform_league_id": league_id,
        "name": name,
        "season": "2026",
        "teams": [
            {
                "platform_team_id": "1",
                "name": team_name,
                "is_mine": True,
                "roster_json": [],
                "points_for": 0.0,
                "opponent_name": None,
                "opponent_points": None,
                "week": 1,
            }
        ],
    }


def test_one_bad_league_does_not_block_the_others(monkeypatch):
    """A single failing league ID must not abort the whole sync -- the healthy
    leagues still have to be committed."""
    from app.models import SyncLog

    init_db()
    db = get_sessionmaker()()
    db.add(AppSetting(key="sleeper_username", value="myuser"))
    db.add(AppSetting(key="sleeper_league_ids", value="bad,good"))
    db.commit()

    def normalize(league_id, my_user_id, players_map):
        if league_id == "bad":
            raise sleeper_adapter.SleeperAdapterError("league 404")
        return _normalized(league_id, name="Good League")

    monkeypatch.setattr(sleeper_adapter, "get_user_id", lambda username: "u1")
    monkeypatch.setattr(sleeper_adapter, "get_players_map", lambda: {})
    monkeypatch.setattr(sleeper_adapter, "normalize_league", normalize)

    sync_sleeper(db)  # must not raise

    # The healthy league survived the bad one.
    good = db.query(League).filter(League.platform_league_id == "good").first()
    assert good is not None
    assert good.name == "Good League"
    assert db.query(Team).filter(Team.league_id == good.id).count() == 1
    # The bad league was not partially written.
    assert db.query(League).filter(League.platform_league_id == "bad").first() is None

    log = db.query(SyncLog).filter(SyncLog.platform == "sleeper").first()
    assert log.success is False
    assert "bad" in log.error and "league 404" in log.error
    db.close()


def test_league_ids_are_stripped_of_whitespace(monkeypatch):
    """'123, 456' must not yield a ' 456' league ID -- the stray space would
    corrupt the Sleeper URL path."""
    init_db()
    db = get_sessionmaker()()
    db.add(AppSetting(key="sleeper_username", value="myuser"))
    db.add(AppSetting(key="sleeper_league_ids", value="123, 456"))
    db.commit()

    seen = []

    def normalize(league_id, my_user_id, players_map):
        seen.append(league_id)
        return _normalized(league_id)

    monkeypatch.setattr(sleeper_adapter, "get_user_id", lambda username: "u1")
    monkeypatch.setattr(sleeper_adapter, "get_players_map", lambda: {})
    monkeypatch.setattr(sleeper_adapter, "normalize_league", normalize)

    sync_sleeper(db)

    assert seen == ["123", "456"]
    db.close()


def test_sync_sleeper_survives_db_error_during_cleanup(monkeypatch):
    """If the session itself starts failing, neither the except block's
    rollback nor the finally block's commit may propagate -- the inline
    startup call would otherwise abort application startup."""
    from sqlalchemy.exc import OperationalError

    init_db()
    db = get_sessionmaker()()
    db.add(AppSetting(key="sleeper_username", value="myuser"))
    db.add(AppSetting(key="sleeper_league_ids", value="999"))
    db.commit()

    def locked(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("database is locked"))

    commits = {"n": 0}
    real_commit = db.commit

    def flaky_commit(*args, **kwargs):
        commits["n"] += 1
        if commits["n"] == 1:  # let the initial SyncLog insert land
            return real_commit()
        locked()

    def raise_adapter_error(username):
        raise sleeper_adapter.SleeperAdapterError("boom")

    monkeypatch.setattr(sleeper_adapter, "get_user_id", raise_adapter_error)
    monkeypatch.setattr(db, "commit", flaky_commit)
    monkeypatch.setattr(db, "rollback", locked)

    # Configured -> SyncLog created -> get_user_id raises -> except ->
    # rollback raises -> finally -> commit raises. Nothing may escape.
    sync_sleeper(db)

    assert commits["n"] >= 2, "the finally-block commit should have been attempted"
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


def test_sync_sleeper_writes_no_synclog_when_unconfigured():
    """A user who never configures Sleeper (now a real scenario, since ESPN
    can be the only platform someone sets up) must not see it reported as a
    failing platform forever -- no SyncLog row at all when nothing is
    configured."""
    from app.models import SyncLog

    init_db()
    db = get_sessionmaker()()

    sync_sleeper(db)

    assert db.query(SyncLog).filter(SyncLog.platform == "sleeper").count() == 0
    db.close()


def test_sync_all_platforms_during_live_window_calls_sync_when_live(monkeypatch):
    from app import sync as sync_module

    calls = []
    monkeypatch.setattr(sync_module, "is_likely_live_window", lambda: True)
    monkeypatch.setattr(sync_module, "sync_all_platforms", lambda: calls.append("synced"))

    sync_all_platforms_during_live_window()

    assert calls == ["synced"]


def test_sync_all_platforms_during_live_window_skips_sync_when_not_live(monkeypatch):
    from app import sync as sync_module

    calls = []
    monkeypatch.setattr(sync_module, "is_likely_live_window", lambda: False)
    monkeypatch.setattr(sync_module, "sync_all_platforms", lambda: calls.append("synced"))

    sync_all_platforms_during_live_window()

    assert calls == []


def test_sync_all_platforms_skips_when_a_sync_is_already_in_progress(monkeypatch):
    """The 60s live-window job and the 20-minute baseline job land on the
    exact same instant every 20 minutes (1200s is a multiple of 60s) --
    without this lock, that's two threads writing to the same SQLite rows
    at once."""
    from app import sync as sync_module

    calls = []
    monkeypatch.setattr(sync_module, "sync_sleeper", lambda db: calls.append("sleeper"))
    monkeypatch.setattr(sync_module, "sync_espn", lambda db: calls.append("espn"))

    assert sync_module._sync_lock.acquire(blocking=False)
    try:
        sync_module.sync_all_platforms()  # must not raise, must not run
    finally:
        sync_module._sync_lock.release()

    assert calls == []


def test_sync_all_platforms_runs_when_lock_is_free(monkeypatch):
    from app import sync as sync_module

    calls = []
    monkeypatch.setattr(sync_module, "sync_sleeper", lambda db: calls.append("sleeper"))
    monkeypatch.setattr(sync_module, "sync_espn", lambda db: calls.append("espn"))
    monkeypatch.setattr(sync_module, "sync_yahoo", lambda db: calls.append("yahoo"))

    sync_module.sync_all_platforms()

    assert calls == ["sleeper", "espn", "yahoo"]
    assert not sync_module._sync_lock.locked()  # released after a normal run
