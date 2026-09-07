from datetime import datetime, timedelta, timezone

from app.db import get_sessionmaker, init_db
from app.models import SyncLog

TEST_PASSWORD = "testpassword123"


def _seed_sync_log(success, error=None, age_minutes=5):
    init_db()
    db = get_sessionmaker()()
    finished = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    db.add(
        SyncLog(
            platform="sleeper",
            started_at=finished - timedelta(seconds=5),
            finished_at=finished,
            success=success,
            error=error,
        )
    )
    db.commit()
    db.close()


def test_sync_status_requires_auth(client):
    resp = client.get("/sync-status")
    assert resp.status_code == 401


def test_sync_status_reports_latest_log_per_platform(client):
    _seed_sync_log(success=False, error="Sleeper user lookup failed: 404", age_minutes=1)
    client.post("/auth/login", json={"password": TEST_PASSWORD})

    resp = client.get("/sync-status")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["platform"] == "sleeper"
    assert data[0]["last_success"] is False
    assert data[0]["last_error"] == "Sleeper user lookup failed: 404"
