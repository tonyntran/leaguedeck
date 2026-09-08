from datetime import datetime, timezone

from app.adapters import espn as espn_adapter
from app.crypto import encrypt_value
from app.db import get_sessionmaker, init_db
from app.models import AppSetting, League, Secret, SyncLog, Team
from app.sync import _current_espn_season, sync_all_platforms, sync_espn


def _seed_espn_settings(db, league_ids="999"):
    db.add(AppSetting(key="espn_league_ids", value=league_ids))
    db.add(Secret(key="espn_s2", encrypted_value=encrypt_value("s2val")))
    db.add(Secret(key="espn_swid", encrypted_value=encrypt_value("{GUID}")))
    db.commit()


def _normalized(league_id, name="League", team_name="Me"):
    return {
        "platform": "espn",
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


def test_sync_espn_creates_league_and_teams(monkeypatch):
    init_db()
    db = get_sessionmaker()()
    _seed_espn_settings(db)

    monkeypatch.setattr(
        espn_adapter,
        "normalize_league",
        lambda league_id, season, espn_s2, swid: _normalized(league_id, name="Test League"),
    )

    sync_espn(db)

    league = (
        db.query(League)
        .filter(League.platform == "espn", League.platform_league_id == "999")
        .first()
    )
    assert league is not None
    assert league.name == "Test League"
    teams = db.query(Team).filter(Team.league_id == league.id).all()
    assert len(teams) == 1
    log = db.query(SyncLog).filter(SyncLog.platform == "espn").first()
    assert log.success is True
    db.close()


def test_sync_espn_writes_no_synclog_when_completely_unconfigured():
    """A user who never sets up ESPN must not see it reported as a failing
    platform forever -- no SyncLog row at all when nothing is configured."""
    init_db()
    db = get_sessionmaker()()

    sync_espn(db)

    assert db.query(SyncLog).filter(SyncLog.platform == "espn").count() == 0
    db.close()


def test_sync_espn_writes_no_synclog_when_only_partially_configured():
    """League IDs set but secrets missing counts as 'not configured' too."""
    init_db()
    db = get_sessionmaker()()
    db.add(AppSetting(key="espn_league_ids", value="999"))
    db.commit()

    sync_espn(db)

    assert db.query(SyncLog).filter(SyncLog.platform == "espn").count() == 0
    db.close()


def test_one_bad_espn_league_does_not_block_the_others(monkeypatch):
    init_db()
    db = get_sessionmaker()()
    _seed_espn_settings(db, league_ids="bad,good")

    def normalize(league_id, season, espn_s2, swid):
        if league_id == "bad":
            raise espn_adapter.EspnAdapterError("league 404")
        return _normalized(league_id, name="Good League")

    monkeypatch.setattr(espn_adapter, "normalize_league", normalize)

    sync_espn(db)  # must not raise

    good = (
        db.query(League)
        .filter(League.platform == "espn", League.platform_league_id == "good")
        .first()
    )
    assert good is not None
    assert (
        db.query(League)
        .filter(League.platform == "espn", League.platform_league_id == "bad")
        .first()
        is None
    )

    log = db.query(SyncLog).filter(SyncLog.platform == "espn").first()
    assert log.success is False
    assert "bad" in log.error
    db.close()


def test_sync_espn_decrypts_stored_secrets_before_calling_adapter(monkeypatch):
    """Confirms the adapter receives the decrypted cookie values, not the
    ciphertext stored in the Secret table."""
    init_db()
    db = get_sessionmaker()()
    _seed_espn_settings(db)

    seen = {}

    def normalize(league_id, season, espn_s2, swid):
        seen["espn_s2"] = espn_s2
        seen["swid"] = swid
        return _normalized(league_id)

    monkeypatch.setattr(espn_adapter, "normalize_league", normalize)

    sync_espn(db)

    assert seen == {"espn_s2": "s2val", "swid": "{GUID}"}
    db.close()


def test_sync_all_platforms_calls_espn_alongside_sleeper(monkeypatch):
    """A regression here would silently stop ESPN leagues from ever
    refreshing again, with no visible error."""
    calls = []
    monkeypatch.setattr("app.sync.sync_sleeper", lambda db: calls.append("sleeper"))
    monkeypatch.setattr("app.sync.sync_espn", lambda db: calls.append("espn"))

    sync_all_platforms()

    assert calls == ["sleeper", "espn"]


def test_current_espn_season_falls_back_before_march():
    """NFL seasons span roughly September-February, so a January/February
    sync should still target the season that started the previous year."""
    assert _current_espn_season(datetime(2026, 1, 15, tzinfo=timezone.utc)) == 2025
    assert _current_espn_season(datetime(2026, 2, 28, tzinfo=timezone.utc)) == 2025
    assert _current_espn_season(datetime(2026, 3, 1, tzinfo=timezone.utc)) == 2026
    assert _current_espn_season(datetime(2026, 9, 1, tzinfo=timezone.utc)) == 2026
