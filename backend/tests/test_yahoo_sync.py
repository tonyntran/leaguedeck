from datetime import datetime, timedelta, timezone

from app.adapters import yahoo as yahoo_adapter
from app.crypto import encrypt_value
from app.db import get_sessionmaker, init_db
from app.models import AppSetting, League, Secret, SyncLog, Team
from app.sync import sync_yahoo


def _seed_yahoo_settings(db, league_ids="999", expires_in_seconds=3600):
    db.add(AppSetting(key="yahoo_client_id", value="my-client-id"))
    db.add(AppSetting(key="yahoo_league_ids", value=league_ids))
    db.add(AppSetting(key="yahoo_guid", value="MY-GUID"))
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
    db.add(AppSetting(key="yahoo_token_expires_at", value=expires_at.isoformat()))
    db.add(Secret(key="yahoo_client_secret", encrypted_value=encrypt_value("shh")))
    db.add(Secret(key="yahoo_access_token", encrypted_value=encrypt_value("at-valid")))
    db.add(Secret(key="yahoo_refresh_token", encrypted_value=encrypt_value("rt-valid")))
    db.commit()


def _normalized(league_id, name="League", team_name="Me"):
    return {
        "platform": "yahoo",
        "platform_league_id": league_id,
        "name": name,
        "season": "2026",
        "teams": [
            {
                "platform_team_id": "nfl.l.999.t.1",
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


def test_sync_yahoo_creates_league_and_teams(monkeypatch):
    init_db()
    db = get_sessionmaker()()
    _seed_yahoo_settings(db)

    seen_access_token = {}

    def normalize(league_id, access_token, my_guid):
        seen_access_token["value"] = access_token
        return _normalized(league_id, name="Test League")

    monkeypatch.setattr(yahoo_adapter, "normalize_league", normalize)

    sync_yahoo(db)

    assert seen_access_token["value"] == "at-valid"  # decrypted before use
    league = (
        db.query(League)
        .filter(League.platform == "yahoo", League.platform_league_id == "999")
        .first()
    )
    assert league is not None
    assert league.name == "Test League"
    teams = db.query(Team).filter(Team.league_id == league.id).all()
    assert len(teams) == 1
    log = db.query(SyncLog).filter(SyncLog.platform == "yahoo").first()
    assert log.success is True
    db.close()


def test_sync_yahoo_writes_no_synclog_when_completely_unconfigured():
    init_db()
    db = get_sessionmaker()()

    sync_yahoo(db)

    assert db.query(SyncLog).filter(SyncLog.platform == "yahoo").count() == 0
    db.close()


def test_sync_yahoo_writes_no_synclog_when_not_yet_authorized():
    """Client credentials saved but the OAuth handshake never completed --
    still counts as 'not configured', same treatment as missing credentials."""
    init_db()
    db = get_sessionmaker()()
    db.add(AppSetting(key="yahoo_client_id", value="my-client-id"))
    db.add(AppSetting(key="yahoo_league_ids", value="999"))
    db.add(Secret(key="yahoo_client_secret", encrypted_value=encrypt_value("shh")))
    db.commit()

    sync_yahoo(db)

    assert db.query(SyncLog).filter(SyncLog.platform == "yahoo").count() == 0
    db.close()


def test_one_bad_yahoo_league_does_not_block_the_others(monkeypatch):
    init_db()
    db = get_sessionmaker()()
    _seed_yahoo_settings(db, league_ids="bad,good")

    def normalize(league_id, access_token, my_guid):
        if league_id == "bad":
            raise yahoo_adapter.YahooAdapterError("league 404")
        return _normalized(league_id, name="Good League")

    monkeypatch.setattr(yahoo_adapter, "normalize_league", normalize)

    sync_yahoo(db)  # must not raise

    good = (
        db.query(League)
        .filter(League.platform == "yahoo", League.platform_league_id == "good")
        .first()
    )
    assert good is not None
    assert (
        db.query(League)
        .filter(League.platform == "yahoo", League.platform_league_id == "bad")
        .first()
        is None
    )

    log = db.query(SyncLog).filter(SyncLog.platform == "yahoo").first()
    assert log.success is False
    assert "bad" in log.error
    db.close()


def test_sync_yahoo_refreshes_token_when_expiring_soon(monkeypatch):
    """A token expiring within the safety margin (5 minutes) must be
    refreshed before normalize_league is called with it."""
    init_db()
    db = get_sessionmaker()()
    _seed_yahoo_settings(db, expires_in_seconds=60)  # expires in 1 minute

    monkeypatch.setattr(
        yahoo_adapter,
        "refresh_access_token",
        lambda client_id, client_secret, refresh_token: {
            "access_token": "at-refreshed",
            "refresh_token": "rt-refreshed",
            "expires_in": 3600,
            "yahoo_guid": None,
        },
    )

    seen_access_token = {}

    def normalize(league_id, access_token, my_guid):
        seen_access_token["value"] = access_token
        return _normalized(league_id)

    monkeypatch.setattr(yahoo_adapter, "normalize_league", normalize)

    sync_yahoo(db)

    assert seen_access_token["value"] == "at-refreshed"
    log = db.query(SyncLog).filter(SyncLog.platform == "yahoo").first()
    assert log.success is True
    db.close()


def test_sync_yahoo_does_not_refresh_when_token_still_valid(monkeypatch):
    init_db()
    db = get_sessionmaker()()
    _seed_yahoo_settings(db, expires_in_seconds=3600)  # not expiring soon

    def raise_if_called(*args, **kwargs):
        raise AssertionError("refresh_access_token should not have been called")

    monkeypatch.setattr(yahoo_adapter, "refresh_access_token", raise_if_called)
    monkeypatch.setattr(yahoo_adapter, "normalize_league", lambda league_id, access_token, my_guid: _normalized(league_id))

    sync_yahoo(db)  # must not raise

    log = db.query(SyncLog).filter(SyncLog.platform == "yahoo").first()
    assert log.success is True
    db.close()


def test_sync_yahoo_records_reauthorize_message_when_refresh_fails(monkeypatch):
    init_db()
    db = get_sessionmaker()()
    _seed_yahoo_settings(db, expires_in_seconds=60)

    def raise_error(client_id, client_secret, refresh_token):
        raise Exception("invalid_grant")

    monkeypatch.setattr(yahoo_adapter, "refresh_access_token", raise_error)

    sync_yahoo(db)  # must not raise

    log = db.query(SyncLog).filter(SyncLog.platform == "yahoo").first()
    assert log.success is False
    assert "reconnect" in log.error.lower() or "authoriz" in log.error.lower()
    db.close()


def test_sync_all_platforms_calls_yahoo_alongside_sleeper_and_espn(monkeypatch):
    calls = []
    monkeypatch.setattr("app.sync.sync_sleeper", lambda db: calls.append("sleeper"))
    monkeypatch.setattr("app.sync.sync_espn", lambda db: calls.append("espn"))
    monkeypatch.setattr("app.sync.sync_yahoo", lambda db: calls.append("yahoo"))

    from app.sync import sync_all_platforms

    sync_all_platforms()

    assert calls == ["sleeper", "espn", "yahoo"]
