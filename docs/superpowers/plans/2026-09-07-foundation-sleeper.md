# LeagueDeck Foundation & Sleeper Read Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up LeagueDeck's backend/frontend skeleton — app-level login, encrypted secrets storage, and a database — and wire the first end-to-end platform (Sleeper, read-only) so a real dashboard screen shows the user's actual rosters and matchups, deployable via `docker-compose up`.

**Architecture:** FastAPI + SQLAlchemy/SQLite backend with a per-platform adapter pattern (`app/adapters/`), an APScheduler job that polls adapters and writes normalized rows to SQLite, and a React/Vite/Tailwind/daisyUI frontend that reads from the backend's REST API. No browser extension anywhere. Sleeper needs no credentials (public API); the app itself is gated by a single-user password-protected session cookie.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0, pydantic-settings, httpx, APScheduler, passlib[bcrypt], itsdangerous, cryptography (Fernet), pytest. React 18, Vite, Tailwind CSS, daisyUI, react-router-dom. Docker/docker-compose for deployment.

**Spec:** `docs/superpowers/specs/2026-09-07-leaguedeck-design.md` (reviewed and revised — read it for the full platform-capability research and risk callouts this plan builds on).

## Scope note

The spec covers three platforms (Sleeper/ESPN/Yahoo) and four dashboard features. Per the writing-plans scope-check, that's multiple independent subsystems and shouldn't be crammed into one plan. **This plan builds only the foundation plus the Sleeper vertical** — the simplest platform (no auth), enough to prove the whole architecture end-to-end with one real, working screen. ESPN's adapter, Yahoo's adapter, and dashboard features 2–4 (injury alerts, start/sit, waiver wire) are each follow-on plans, written when we get there.

One consequence: this plan does **not** build the ffverse/nflverse player-ID crosswalk from the spec's data-model section. That crosswalk exists to reconcile the *same* player's different IDs *across* platforms — with only Sleeper implemented, there's nothing to reconcile yet. Sleeper's own player IDs are used directly. The crosswalk is a required task in the ESPN-adapter follow-on plan (its first job is to join a second platform's IDs to Sleeper's).

Also deferred: Yahoo's OAuth needs an HTTPS callback (from the spec's Deployment section) — irrelevant until the Yahoo plan actually implements OAuth, so no reverse proxy/cert work happens here.

## Global Constraints

- Single-user app: no multi-user auth system. One password, hashed, checked against every protected API route via a session-cookie dependency.
- ESPN cookies and Yahoo tokens must be encrypted at rest (Fernet) — not applicable yet in this plan (no ESPN/Yahoo adapters), but the `secrets` table and `crypto.py` helper are built now so later plans just use them.
- No browser extension, anywhere, ever, for this project.
- Sleeper reads require no credentials — only a username and league ID(s), entered once in Settings.
- No CI-driven testing of anything that talks to a live third-party UI (N/A in this plan — no browser automation exists yet).
- YAGNI: no Alembic/migrations for a single-user SQLite database — `Base.metadata.create_all()` on startup is sufficient and is what this plan uses.
- Frontend visual design (colors, theme, layout aesthetic) is **not** decided by the engineer unilaterally — Task 10 is an explicit, mandatory gate where multiple design directions are presented to the user for selection before any styling is applied. Tasks 1–9 build fully working but visually bare (unstyled/semantic-HTML) screens for exactly this reason.

---

## Task 1: Backend project scaffold

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_health.py`
- Create: `.env.example`
- Create: `.gitignore`

**Interfaces:**
- Produces: `app.main:app` (the FastAPI instance every later task adds routers to), `GET /health` endpoint.

- [ ] **Step 1: Create the backend folder structure and dependency list**

```bash
mkdir -p ~/projects/LeagueDeck/backend/app/routers
mkdir -p ~/projects/LeagueDeck/backend/app/adapters
mkdir -p ~/projects/LeagueDeck/backend/tests
mkdir -p ~/projects/LeagueDeck/backend/data
```

`backend/requirements.txt`:

```
fastapi
uvicorn[standard]
sqlalchemy
pydantic-settings
httpx
apscheduler
passlib[bcrypt]
itsdangerous
cryptography
pytest
```

`backend/pyproject.toml`:

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

`.gitignore` (repo root):

```
__pycache__/
*.pyc
.env
backend/data/*.db
backend/data/sleeper_players_cache.json
frontend/node_modules/
frontend/dist/
```

`.env.example` (repo root):

```
DATABASE_URL=sqlite:///./data/leaguedeck.db
APP_PASSWORD_HASH=
SECRET_KEY=
FERNET_KEY=
ENABLE_SCHEDULER=true
```

- [ ] **Step 2: Install dependencies**

```bash
cd ~/projects/LeagueDeck/backend
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

- [ ] **Step 3: Write the failing health-check test**

`backend/tests/conftest.py`:

```python
import pytest
from cryptography.fernet import Fernet
from passlib.context import CryptContext

TEST_PASSWORD = "testpassword123"
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@pytest.fixture(autouse=True)
def test_env(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("APP_PASSWORD_HASH", _pwd_context.hash(TEST_PASSWORD))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("ENABLE_SCHEDULER", "false")
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        yield c
```

`backend/tests/test_health.py`:

```python
def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd ~/projects/LeagueDeck/backend && pytest tests/test_health.py -v`
Expected: FAIL (no module named `app.main` / no config yet — that's fine at this step, we haven't written `app.config` which conftest indirectly requires once `app.main` is created next)

- [ ] **Step 5: Write minimal config and app to make it pass**

`backend/app/__init__.py`: empty file.

`backend/app/config.py`:

```python
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/leaguedeck.db"
    app_password_hash: str
    secret_key: str
    fernet_key: str
    enable_scheduler: bool = True

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`backend/app/main.py`:

```python
from fastapi import FastAPI

app = FastAPI(title="LeagueDeck")


@app.get("/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_health.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
cd ~/projects/LeagueDeck
git add backend/requirements.txt backend/pyproject.toml backend/app backend/tests .env.example .gitignore
git commit -m "Scaffold backend: FastAPI app, config, health check"
```

---

## Task 2: Database layer

**Files:**
- Create: `backend/app/db.py`
- Create: `backend/app/models.py`
- Create: `backend/tests/test_models.py`

**Interfaces:**
- Consumes: `app.config.get_settings()` (Task 1).
- Produces: `app.db.Base`, `app.db.get_engine()`, `app.db.get_sessionmaker()`, `app.db.get_db()` (FastAPI dependency), `app.db.init_db()`. Models: `League`, `Team`, `SyncLog`, `Secret`, `AppSetting` — every later task's DB access goes through these.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_models.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 3: Write the implementation**

`backend/app/db.py`:

```python
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import get_settings

Base = declarative_base()


@lru_cache
def get_engine():
    return create_engine(
        get_settings().database_url,
        connect_args={"check_same_thread": False},
    )


def get_sessionmaker():
    return sessionmaker(autocommit=False, autoflush=False, bind=get_engine())


def get_db():
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


def init_db():
    Base.metadata.create_all(bind=get_engine())
```

`backend/app/models.py`:

```python
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class League(Base):
    __tablename__ = "leagues"

    id = Column(Integer, primary_key=True)
    platform = Column(String, nullable=False)
    platform_league_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    season = Column(String, nullable=False)

    teams = relationship("Team", back_populates="league", cascade="all, delete-orphan")


class Team(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True)
    league_id = Column(Integer, ForeignKey("leagues.id"), nullable=False)
    platform_team_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    is_mine = Column(Boolean, default=False)
    roster_json = Column(Text, nullable=False, default="[]")
    points_for = Column(Float, default=0.0)
    opponent_name = Column(String, nullable=True)
    opponent_points = Column(Float, nullable=True)
    week = Column(Integer, nullable=True)

    league = relationship("League", back_populates="teams")


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id = Column(Integer, primary_key=True)
    platform = Column(String, nullable=False)
    started_at = Column(DateTime, default=_utcnow)
    finished_at = Column(DateTime, nullable=True)
    success = Column(Boolean, nullable=True)
    error = Column(Text, nullable=True)


class Secret(Base):
    """Encrypted platform credentials (ESPN cookies, Yahoo tokens). Unused until
    the ESPN/Yahoo plans, but created now so this table exists from day one."""

    __tablename__ = "secrets"

    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False)
    encrypted_value = Column(Text, nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)


class AppSetting(Base):
    __tablename__ = "app_settings"

    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(Text, nullable=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_models.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/db.py backend/app/models.py backend/tests/test_models.py
git commit -m "Add SQLAlchemy models and DB session handling"
```

---

## Task 3: Secrets encryption helper

**Files:**
- Create: `backend/app/crypto.py`
- Create: `backend/tests/test_crypto.py`

**Interfaces:**
- Consumes: `app.config.get_settings().fernet_key`.
- Produces: `encrypt_value(plaintext: str) -> str`, `decrypt_value(ciphertext: str) -> str` — used by any future code writing to the `Secret` table.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_crypto.py`:

```python
from app.crypto import decrypt_value, encrypt_value


def test_encrypt_decrypt_roundtrip():
    ciphertext = encrypt_value("my-secret-cookie-value")
    assert ciphertext != "my-secret-cookie-value"
    assert decrypt_value(ciphertext) == "my-secret-cookie-value"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_crypto.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.crypto'`

- [ ] **Step 3: Write the implementation**

`backend/app/crypto.py`:

```python
from cryptography.fernet import Fernet

from app.config import get_settings


def encrypt_value(plaintext: str) -> str:
    fernet = Fernet(get_settings().fernet_key.encode())
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str) -> str:
    fernet = Fernet(get_settings().fernet_key.encode())
    return fernet.decrypt(ciphertext.encode()).decode()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_crypto.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/crypto.py backend/tests/test_crypto.py
git commit -m "Add Fernet-based encryption helper for stored platform secrets"
```

---

## Task 4: App-level authentication

**Files:**
- Create: `backend/app/auth.py`
- Create: `backend/app/routers/__init__.py`
- Create: `backend/app/routers/auth.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_auth.py`

**Interfaces:**
- Consumes: `app.config.get_settings()` (Task 1).
- Produces: `require_auth` (FastAPI dependency — every protected router in later tasks lists `dependencies=[Depends(require_auth)]`), `SESSION_COOKIE_NAME`, routes `POST /auth/login`, `POST /auth/logout`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_auth.py`:

```python
TEST_PASSWORD = "testpassword123"


def test_login_with_correct_password_sets_session_cookie(client):
    resp = client.post("/auth/login", json={"password": TEST_PASSWORD})
    assert resp.status_code == 200
    assert "leaguedeck_session" in resp.cookies


def test_login_with_wrong_password_is_rejected(client):
    resp = client.post("/auth/login", json={"password": "wrong-password"})
    assert resp.status_code == 401


def test_logout_clears_session_cookie(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})
    resp = client.post("/auth/logout")
    assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_auth.py -v`
Expected: FAIL with `404 Not Found` for `/auth/login` (route doesn't exist yet)

- [ ] **Step 3: Write the implementation**

`backend/app/auth.py`:

```python
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from fastapi import HTTPException, Request, status
from passlib.context import CryptContext

from app.config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SESSION_COOKIE_NAME = "leaguedeck_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days


def verify_password(plain_password: str) -> bool:
    return pwd_context.verify(plain_password, get_settings().app_password_hash)


def create_session_token() -> str:
    serializer = URLSafeTimedSerializer(get_settings().secret_key)
    return serializer.dumps({"authenticated": True})


def verify_session_token(token: str) -> bool:
    serializer = URLSafeTimedSerializer(get_settings().secret_key)
    try:
        data = serializer.loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return False
    return bool(data.get("authenticated"))


def require_auth(request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token or not verify_session_token(token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
```

`backend/app/routers/__init__.py`: empty file.

`backend/app/routers/auth.py`:

```python
from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel

from app.auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    create_session_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
def login(payload: LoginRequest, response: Response):
    if not verify_password(payload.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")
    token = create_session_token()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE_SECONDS,
    )
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}
```

`backend/app/main.py` (replace contents):

```python
from fastapi import FastAPI

from app.routers import auth as auth_router

app = FastAPI(title="LeagueDeck")

app.include_router(auth_router.router)


@app.get("/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_auth.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/auth.py backend/app/routers backend/app/main.py backend/tests/test_auth.py
git commit -m "Add app-level password login with signed session cookie"
```

---

## Task 5: Settings API (Sleeper username/league IDs)

**Files:**
- Create: `backend/app/routers/settings.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_settings_router.py`

**Interfaces:**
- Consumes: `app.db.get_db` (Task 2), `app.auth.require_auth` (Task 4), `app.models.AppSetting` (Task 2).
- Produces: `GET /settings/sleeper`, `PUT /settings/sleeper` — Task 7's sync job reads the same `AppSetting` rows this task writes.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_settings_router.py`:

```python
TEST_PASSWORD = "testpassword123"


def test_settings_requires_auth(client):
    resp = client.get("/settings/sleeper")
    assert resp.status_code == 401


def test_put_and_get_sleeper_settings_roundtrip(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})

    put_resp = client.put(
        "/settings/sleeper", json={"username": "myuser", "league_ids": ["111", "222"]}
    )
    assert put_resp.status_code == 200

    get_resp = client.get("/settings/sleeper")
    assert get_resp.status_code == 200
    assert get_resp.json() == {"username": "myuser", "league_ids": ["111", "222"]}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_settings_router.py -v`
Expected: FAIL with `404 Not Found`

- [ ] **Step 3: Write the implementation**

`backend/app/routers/settings.py`:

```python
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.db import get_db
from app.models import AppSetting

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_auth)])


class SleeperSettings(BaseModel):
    username: str
    league_ids: list[str]


def _set_setting(db: Session, key: str, value: str) -> None:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def _get_setting(db: Session, key: str) -> str | None:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else None


@router.get("/sleeper")
def get_sleeper_settings(db: Session = Depends(get_db)):
    username = _get_setting(db, "sleeper_username") or ""
    league_ids_raw = _get_setting(db, "sleeper_league_ids") or ""
    return {
        "username": username,
        "league_ids": [x for x in league_ids_raw.split(",") if x],
    }


@router.put("/sleeper")
def set_sleeper_settings(payload: SleeperSettings, db: Session = Depends(get_db)):
    _set_setting(db, "sleeper_username", payload.username)
    _set_setting(db, "sleeper_league_ids", ",".join(payload.league_ids))
    return {"ok": True}
```

`backend/app/main.py` (add the router):

```python
from fastapi import FastAPI

from app.routers import auth as auth_router
from app.routers import settings as settings_router

app = FastAPI(title="LeagueDeck")

app.include_router(auth_router.router)
app.include_router(settings_router.router)


@app.get("/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_settings_router.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/settings.py backend/app/main.py backend/tests/test_settings_router.py
git commit -m "Add Settings API for Sleeper username/league IDs"
```

---

## Task 6: Sleeper adapter

**Files:**
- Create: `backend/app/adapters/__init__.py`
- Create: `backend/app/adapters/sleeper.py`
- Create: `backend/tests/test_sleeper_adapter.py`

**Interfaces:**
- Produces: `get_user_id(username: str) -> str`, `get_players_map() -> dict`, `normalize_league(league_id: str, my_user_id: str, players_map: dict) -> dict` (returns `{"platform", "platform_league_id", "name", "season", "teams": [...]}` where each team dict has keys `platform_team_id, name, is_mine, roster_json (list), points_for, opponent_name, opponent_points, week`). Task 7's sync job calls these three functions directly.

- [ ] **Step 1: Write the failing tests**

`backend/app/adapters/__init__.py`: empty file.

`backend/tests/test_sleeper_adapter.py`:

```python
import httpx

from app.adapters import sleeper


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def test_get_user_id_returns_user_id(monkeypatch):
    def fake_get(url, timeout=10.0):
        assert url == f"{sleeper.SLEEPER_BASE_URL}/user/testuser"
        return FakeResponse({"user_id": "12345"})

    monkeypatch.setattr(httpx, "get", fake_get)
    assert sleeper.get_user_id("testuser") == "12345"


def test_normalize_league_builds_teams_with_opponent_and_roster(monkeypatch):
    league_id = "999"
    responses = {
        f"{sleeper.SLEEPER_BASE_URL}/league/{league_id}": FakeResponse(
            {"name": "Test League", "season": "2026", "settings": {"leg": 1}}
        ),
        f"{sleeper.SLEEPER_BASE_URL}/league/{league_id}/rosters": FakeResponse(
            [
                {"roster_id": 1, "owner_id": "u1", "players": ["p1"]},
                {"roster_id": 2, "owner_id": "u2", "players": ["p2"]},
            ]
        ),
        f"{sleeper.SLEEPER_BASE_URL}/league/{league_id}/users": FakeResponse(
            [
                {"user_id": "u1", "display_name": "Me", "metadata": {}},
                {"user_id": "u2", "display_name": "Rival", "metadata": {}},
            ]
        ),
        f"{sleeper.SLEEPER_BASE_URL}/league/{league_id}/matchups/1": FakeResponse(
            [
                {"roster_id": 1, "matchup_id": 1, "points": 100.5},
                {"roster_id": 2, "matchup_id": 1, "points": 90.2},
            ]
        ),
    }

    def fake_get(url, timeout=10.0):
        return responses[url]

    monkeypatch.setattr(httpx, "get", fake_get)

    players_map = {"p1": {"full_name": "Player One", "position": "RB", "team": "KC"}}
    result = sleeper.normalize_league(league_id, my_user_id="u1", players_map=players_map)

    assert result["name"] == "Test League"
    assert len(result["teams"]) == 2
    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["name"] == "Me"
    assert my_team["opponent_name"] == "Rival"
    assert my_team["opponent_points"] == 90.2
    assert my_team["roster_json"] == [
        {"player_id": "p1", "name": "Player One", "position": "RB", "team": "KC"}
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sleeper_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.adapters.sleeper'`

- [ ] **Step 3: Write the implementation**

`backend/app/adapters/sleeper.py`:

```python
import json
import time
from pathlib import Path

import httpx

SLEEPER_BASE_URL = "https://api.sleeper.app/v1"
PLAYERS_CACHE_PATH = Path("data/sleeper_players_cache.json")
PLAYERS_CACHE_MAX_AGE_SECONDS = 24 * 60 * 60  # Sleeper asks this endpoint not be hit often


class SleeperAdapterError(Exception):
    pass


def get_user_id(username: str) -> str:
    resp = httpx.get(f"{SLEEPER_BASE_URL}/user/{username}", timeout=10.0)
    if resp.status_code != 200:
        raise SleeperAdapterError(f"Sleeper user lookup failed: {resp.status_code}")
    data = resp.json()
    if not data:
        raise SleeperAdapterError(f"Sleeper user '{username}' not found")
    return data["user_id"]


def get_players_map() -> dict:
    """Returns {player_id: {"full_name", "position", "team"}}, cached on disk
    for up to 24h since Sleeper's docs ask this bulk endpoint not be polled often."""
    if PLAYERS_CACHE_PATH.exists():
        age = time.time() - PLAYERS_CACHE_PATH.stat().st_mtime
        if age < PLAYERS_CACHE_MAX_AGE_SECONDS:
            return json.loads(PLAYERS_CACHE_PATH.read_text())

    resp = httpx.get(f"{SLEEPER_BASE_URL}/players/nfl", timeout=30.0)
    resp.raise_for_status()
    raw = resp.json()
    players_map = {
        pid: {
            "full_name": p.get("full_name") or p.get("last_name") or pid,
            "position": p.get("position"),
            "team": p.get("team"),
        }
        for pid, p in raw.items()
    }
    PLAYERS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAYERS_CACHE_PATH.write_text(json.dumps(players_map))
    return players_map


def _get_league(league_id: str) -> dict:
    resp = httpx.get(f"{SLEEPER_BASE_URL}/league/{league_id}", timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def _get_rosters(league_id: str) -> list[dict]:
    resp = httpx.get(f"{SLEEPER_BASE_URL}/league/{league_id}/rosters", timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def _get_league_users(league_id: str) -> list[dict]:
    resp = httpx.get(f"{SLEEPER_BASE_URL}/league/{league_id}/users", timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def _get_matchups(league_id: str, week: int) -> list[dict]:
    resp = httpx.get(f"{SLEEPER_BASE_URL}/league/{league_id}/matchups/{week}", timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def normalize_league(league_id: str, my_user_id: str, players_map: dict) -> dict:
    """Fetch everything for one Sleeper league and normalize into LeagueDeck's shape."""
    league_data = _get_league(league_id)
    rosters = _get_rosters(league_id)
    users = _get_league_users(league_id)
    week = league_data["settings"]["leg"]  # Sleeper's name for "current week"
    matchups = _get_matchups(league_id, week)

    users_by_id = {u["user_id"]: u for u in users}
    matchups_by_roster_id = {m["roster_id"]: m for m in matchups}
    matchups_by_matchup_id: dict[int, list[dict]] = {}
    for m in matchups:
        matchups_by_matchup_id.setdefault(m["matchup_id"], []).append(m)

    teams = []
    for roster in rosters:
        owner = users_by_id.get(roster["owner_id"], {})
        team_name = (
            owner.get("metadata", {}).get("team_name")
            or owner.get("display_name")
            or f"Roster {roster['roster_id']}"
        )

        my_matchup = matchups_by_roster_id.get(roster["roster_id"])
        opponent_name = None
        opponent_points = None
        my_points = my_matchup["points"] if my_matchup else 0.0

        if my_matchup:
            group = matchups_by_matchup_id.get(my_matchup["matchup_id"], [])
            opponent = next((m for m in group if m["roster_id"] != roster["roster_id"]), None)
            if opponent:
                opp_roster = next(
                    (r for r in rosters if r["roster_id"] == opponent["roster_id"]), None
                )
                if opp_roster:
                    opp_owner = users_by_id.get(opp_roster["owner_id"], {})
                    opponent_name = opp_owner.get("metadata", {}).get(
                        "team_name"
                    ) or opp_owner.get("display_name")
                opponent_points = opponent["points"]

        roster_players = [
            {
                "player_id": pid,
                "name": players_map.get(pid, {}).get("full_name", pid),
                "position": players_map.get(pid, {}).get("position"),
                "team": players_map.get(pid, {}).get("team"),
            }
            for pid in (roster.get("players") or [])
        ]

        teams.append(
            {
                "platform_team_id": str(roster["roster_id"]),
                "name": team_name,
                "is_mine": roster["owner_id"] == my_user_id,
                "roster_json": roster_players,
                "points_for": my_points,
                "opponent_name": opponent_name,
                "opponent_points": opponent_points,
                "week": week,
            }
        )

    return {
        "platform": "sleeper",
        "platform_league_id": league_id,
        "name": league_data["name"],
        "season": league_data["season"],
        "teams": teams,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sleeper_adapter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/adapters backend/tests/test_sleeper_adapter.py
git commit -m "Add Sleeper adapter: fetch + normalize leagues, rosters, matchups"
```

---

## Task 7: Sync job

**Files:**
- Create: `backend/app/sync.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_sync.py`

**Interfaces:**
- Consumes: `app.db.get_sessionmaker` (Task 2), `app.models.{League,Team,SyncLog,AppSetting}` (Task 2), `app.adapters.sleeper.{get_user_id,get_players_map,normalize_league}` (Task 6).
- Produces: `sync_sleeper(db: Session) -> None`, `sync_all_platforms() -> None` — Task 8's API reads what this writes; `main.py`'s startup schedules `sync_all_platforms` to run every 20 minutes via APScheduler.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_sync.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sync.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.sync'`

- [ ] **Step 3: Write the implementation**

`backend/app/sync.py`:

```python
import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.adapters import sleeper as sleeper_adapter
from app.db import get_sessionmaker
from app.models import AppSetting, League, SyncLog, Team


def _get_setting(db: Session, key: str) -> str | None:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else None


def sync_sleeper(db: Session) -> None:
    log = SyncLog(platform="sleeper", started_at=datetime.now(timezone.utc))
    db.add(log)
    db.commit()

    try:
        username = _get_setting(db, "sleeper_username")
        league_ids_raw = _get_setting(db, "sleeper_league_ids") or ""
        league_ids = [x for x in league_ids_raw.split(",") if x]
        if not username or not league_ids:
            raise ValueError("Sleeper username/league IDs not configured")

        my_user_id = sleeper_adapter.get_user_id(username)
        players_map = sleeper_adapter.get_players_map()

        for league_id in league_ids:
            normalized = sleeper_adapter.normalize_league(league_id, my_user_id, players_map)

            league = (
                db.query(League)
                .filter(League.platform == "sleeper", League.platform_league_id == league_id)
                .first()
            )
            if league is None:
                league = League(
                    platform="sleeper",
                    platform_league_id=league_id,
                    name=normalized["name"],
                    season=normalized["season"],
                )
                db.add(league)
                db.flush()
            else:
                league.name = normalized["name"]
                league.season = normalized["season"]
                db.query(Team).filter(Team.league_id == league.id).delete()

            for team_data in normalized["teams"]:
                roster_players = team_data.pop("roster_json")
                db.add(
                    Team(
                        league_id=league.id,
                        roster_json=json.dumps(roster_players),
                        **team_data,
                    )
                )

        db.commit()
        log.success = True
    except Exception as exc:  # sync must never crash the scheduler
        db.rollback()
        log.success = False
        log.error = str(exc)
    finally:
        log.finished_at = datetime.now(timezone.utc)
        db.commit()


def sync_all_platforms() -> None:
    db = get_sessionmaker()()
    try:
        sync_sleeper(db)
        # ESPN and Yahoo adapters are added by their own follow-on plans.
    finally:
        db.close()
```

`backend/app/main.py` (final version for this plan — wires startup sync + scheduler):

```python
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import init_db
from app.routers import auth as auth_router
from app.routers import settings as settings_router
from app.sync import sync_all_platforms

scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if get_settings().enable_scheduler:
        scheduler.add_job(sync_all_platforms, "interval", minutes=20, id="sync_all_platforms")
        scheduler.start()
        sync_all_platforms()
    yield
    if scheduler.running:
        scheduler.shutdown()


app = FastAPI(title="LeagueDeck", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(settings_router.router)


@app.get("/health")
def health():
    return {"status": "ok"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sync.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite to confirm nothing else broke**

Run: `pytest -v`
Expected: All tests PASS (health, models, crypto, auth, settings, sleeper adapter, sync)

- [ ] **Step 6: Commit**

```bash
git add backend/app/sync.py backend/app/main.py backend/tests/test_sync.py
git commit -m "Add APScheduler sync job for Sleeper, wired into app startup"
```

---

## Task 8: Leagues API and sync status

The spec requires sync failures to surface visibly, not just sit in a log
table nobody checks (see "Sync failures must surface" in the design doc).
This task adds that alongside the leagues listing, since both are read
endpoints over data Task 7 already writes.

**Files:**
- Create: `backend/app/routers/leagues.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_leagues_router.py`
- Create: `backend/tests/test_sync_status_router.py`

**Interfaces:**
- Consumes: `app.db.get_db`, `app.auth.require_auth`, `app.models.{League,Team,SyncLog}` (all prior tasks).
- Produces: `GET /leagues` returning `[{id, platform, name, season, teams: [{id, name, is_mine, roster, points_for, opponent_name, opponent_points, week}]}]`, and `GET /sync-status` returning `[{platform, last_success_at, last_success, last_error}]` — Task 9's frontend `DashboardPage` renders both.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_leagues_router.py`:

```python
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
    assert data[0]["teams"][0]["roster"][0]["name"] == "Player One"
    assert data[0]["teams"][0]["opponent_name"] == "Rival"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_leagues_router.py -v`
Expected: FAIL with `404 Not Found`

- [ ] **Step 3: Write the implementation**

`backend/app/routers/leagues.py`:

```python
import json

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.db import get_db
from app.models import League, Team

router = APIRouter(prefix="/leagues", tags=["leagues"], dependencies=[Depends(require_auth)])


@router.get("")
def list_leagues(db: Session = Depends(get_db)):
    leagues = db.query(League).all()
    result = []
    for league in leagues:
        teams = db.query(Team).filter(Team.league_id == league.id).all()
        result.append(
            {
                "id": league.id,
                "platform": league.platform,
                "name": league.name,
                "season": league.season,
                "teams": [
                    {
                        "id": t.id,
                        "name": t.name,
                        "is_mine": t.is_mine,
                        "roster": json.loads(t.roster_json),
                        "points_for": t.points_for,
                        "opponent_name": t.opponent_name,
                        "opponent_points": t.opponent_points,
                        "week": t.week,
                    }
                    for t in teams
                ],
            }
        )
    return result
```

`backend/app/main.py` (add the router — insert alongside the other `include_router` calls):

```python
from app.routers import leagues as leagues_router
```

and

```python
app.include_router(leagues_router.router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_leagues_router.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing sync-status test**

`backend/tests/test_sync_status_router.py`:

```python
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
```

- [ ] **Step 6: Run test to verify it fails**

Run: `pytest tests/test_sync_status_router.py -v`
Expected: FAIL with `404 Not Found`

- [ ] **Step 7: Write the sync-status implementation**

In `backend/app/routers/leagues.py`, change the existing `from app.models import League, Team` line to `from app.models import League, SyncLog, Team`, then append:

```python
sync_status_router = APIRouter(
    prefix="/sync-status", tags=["sync"], dependencies=[Depends(require_auth)]
)


@sync_status_router.get("")
def get_sync_status(db: Session = Depends(get_db)):
    platforms = [row[0] for row in db.query(SyncLog.platform).distinct().all()]
    result = []
    for platform in platforms:
        latest = (
            db.query(SyncLog)
            .filter(SyncLog.platform == platform)
            .order_by(SyncLog.started_at.desc())
            .first()
        )
        result.append(
            {
                "platform": platform,
                "last_success_at": latest.finished_at.isoformat() if latest.finished_at else None,
                "last_success": latest.success,
                "last_error": latest.error,
            }
        )
    return result
```

`backend/app/main.py` (Step 3 already added `from app.routers import leagues as leagues_router` and `app.include_router(leagues_router.router)` — add one more line alongside it):

```python
app.include_router(leagues_router.sync_status_router)
```

- [ ] **Step 8: Run test to verify it passes**

Run: `pytest tests/test_sync_status_router.py -v`
Expected: PASS

- [ ] **Step 9: Run the full backend test suite**

Run: `pytest -v`
Expected: All tests PASS

- [ ] **Step 10: Commit**

```bash
git add backend/app/routers/leagues.py backend/app/main.py backend/tests/test_leagues_router.py backend/tests/test_sync_status_router.py
git commit -m "Add GET /leagues and GET /sync-status APIs"
```

---

## Task 9: Frontend scaffold (functional, intentionally unstyled)

**Files:**
- Create: `frontend/` (via Vite scaffold)
- Create: `frontend/src/api/client.js`
- Create: `frontend/src/pages/LoginPage.jsx`
- Create: `frontend/src/pages/SettingsPage.jsx`
- Create: `frontend/src/pages/DashboardPage.jsx`
- Modify: `frontend/src/App.jsx`

**Interfaces:**
- Consumes: `POST /auth/login`, `GET/PUT /settings/sleeper`, `GET /leagues`, `GET /sync-status` (Tasks 4/5/8).
- Produces: three working routes (`/login`, `/settings`, `/`) with zero visual styling — Task 10 applies the chosen design direction on top of these exact components without changing their logic.

**Note:** these pages use raw semantic HTML with no CSS classes at all, on purpose (see Global Constraints — no visual direction is chosen until Task 10). Tailwind/daisyUI are installed here so they're available, but not yet applied to markup.

- [ ] **Step 1: Scaffold the Vite React app and install dependencies**

```bash
cd ~/projects/LeagueDeck
npm create vite@latest frontend -- --template react
cd frontend
npm install
npm install react-router-dom
npm install -D tailwindcss @tailwindcss/vite daisyui
```

`frontend/vite.config.js`:

```js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173 },
})
```

`frontend/src/index.css` (replace contents — Tailwind/daisyUI wired in but unused by any component yet):

```css
@import "tailwindcss";
@plugin "daisyui";
```

- [ ] **Step 2: Write the API client**

`frontend/src/api/client.js`:

```js
const BASE_URL = 'http://localhost:8000'

async function request(path, options = {}) {
  const resp = await fetch(`${BASE_URL}${path}`, {
    ...options,
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...options.headers },
  })
  if (!resp.ok) {
    const error = new Error(`Request to ${path} failed with ${resp.status}`)
    error.status = resp.status
    throw error
  }
  return resp.status === 204 ? null : resp.json()
}

export function login(password) {
  return request('/auth/login', { method: 'POST', body: JSON.stringify({ password }) })
}

export function logout() {
  return request('/auth/logout', { method: 'POST' })
}

export function getSleeperSettings() {
  return request('/settings/sleeper')
}

export function putSleeperSettings(username, leagueIds) {
  return request('/settings/sleeper', {
    method: 'PUT',
    body: JSON.stringify({ username, league_ids: leagueIds }),
  })
}

export function getLeagues() {
  return request('/leagues')
}

export function getSyncStatus() {
  return request('/sync-status')
}
```

- [ ] **Step 3: Write the pages**

`frontend/src/pages/LoginPage.jsx`:

```jsx
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { login } from '../api/client'

export default function LoginPage() {
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const navigate = useNavigate()

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    try {
      await login(password)
      navigate('/')
    } catch (err) {
      setError('Incorrect password')
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <h1>LeagueDeck</h1>
      <label htmlFor="password">Password</label>
      <input
        id="password"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      <button type="submit">Log in</button>
      {error && <p>{error}</p>}
    </form>
  )
}
```

`frontend/src/pages/SettingsPage.jsx`:

```jsx
import { useEffect, useState } from 'react'
import { getSleeperSettings, putSleeperSettings } from '../api/client'

export default function SettingsPage() {
  const [username, setUsername] = useState('')
  const [leagueIdsText, setLeagueIdsText] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    getSleeperSettings().then((data) => {
      setUsername(data.username)
      setLeagueIdsText(data.league_ids.join(', '))
    })
  }, [])

  async function handleSubmit(e) {
    e.preventDefault()
    const leagueIds = leagueIdsText
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    await putSleeperSettings(username, leagueIds)
    setSaved(true)
  }

  return (
    <form onSubmit={handleSubmit}>
      <h1>Settings</h1>
      <label htmlFor="username">Sleeper username</label>
      <input id="username" value={username} onChange={(e) => setUsername(e.target.value)} />
      <label htmlFor="leagueIds">Sleeper league IDs (comma-separated)</label>
      <input
        id="leagueIds"
        value={leagueIdsText}
        onChange={(e) => setLeagueIdsText(e.target.value)}
      />
      <button type="submit">Save</button>
      {saved && <p>Saved.</p>}
    </form>
  )
}
```

`frontend/src/pages/DashboardPage.jsx`:

```jsx
import { useEffect, useState } from 'react'
import { getLeagues, getSyncStatus } from '../api/client'

const STALE_THRESHOLD_MINUTES = 60

function isDegraded(status) {
  if (status.last_success === false) return true
  if (!status.last_success_at) return true
  const ageMinutes = (Date.now() - new Date(status.last_success_at).getTime()) / 60000
  return ageMinutes > STALE_THRESHOLD_MINUTES
}

export default function DashboardPage() {
  const [leagues, setLeagues] = useState(null)
  const [syncStatus, setSyncStatus] = useState([])
  const [error, setError] = useState(null)

  useEffect(() => {
    getLeagues()
      .then(setLeagues)
      .catch(() => setError('Could not load leagues. Log in and configure Settings first.'))
    getSyncStatus()
      .then(setSyncStatus)
      .catch(() => {})
  }, [])

  if (error) return <p>{error}</p>
  if (!leagues) return <p>Loading...</p>

  const degradedPlatforms = syncStatus.filter(isDegraded)

  return (
    <div>
      <h1>My Leagues</h1>
      {degradedPlatforms.map((status) => (
        <p key={status.platform} role="alert">
          {status.platform} last synced{' '}
          {status.last_success_at ? new Date(status.last_success_at).toLocaleString() : 'never'}
          {status.last_error ? ` — ${status.last_error}` : ''} — check Settings.
        </p>
      ))}
      {leagues.map((league) => (
        <section key={league.id}>
          <h2>
            {league.name} ({league.platform}, {league.season})
          </h2>
          {league.teams.map((team) => (
            <article key={team.id}>
              <h3>
                {team.name} {team.is_mine ? '(mine)' : ''}
              </h3>
              <p>
                Week {team.week}: {team.points_for} pts vs {team.opponent_name ?? 'TBD'} (
                {team.opponent_points ?? '-'} pts)
              </p>
              <ul>
                {team.roster.map((player) => (
                  <li key={player.player_id}>
                    {player.name} — {player.position} ({player.team})
                  </li>
                ))}
              </ul>
            </article>
          ))}
        </section>
      ))}
    </div>
  )
}
```

`frontend/src/App.jsx` (replace contents):

```jsx
import { BrowserRouter, Link, Route, Routes } from 'react-router-dom'
import DashboardPage from './pages/DashboardPage'
import LoginPage from './pages/LoginPage'
import SettingsPage from './pages/SettingsPage'

export default function App() {
  return (
    <BrowserRouter>
      <nav>
        <Link to="/">Dashboard</Link> | <Link to="/settings">Settings</Link> |{' '}
        <Link to="/login">Login</Link>
      </nav>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/login" element={<LoginPage />} />
      </Routes>
    </BrowserRouter>
  )
}
```

- [ ] **Step 4: Manually verify the end-to-end flow**

With the backend running (`uvicorn app.main:app --reload` from `backend/`, `.env` populated per Task 1's `.env.example` — see Task 11 for generating real secret values), run:

```bash
cd ~/projects/LeagueDeck/frontend
npm run dev
```

Open `http://localhost:5173/login`, log in with the app password, go to `/settings`, enter a real Sleeper username and league ID, save, then go to `/` and confirm real roster/matchup data renders (it may take a few seconds after saving for the next scheduled sync — or restart the backend to trigger the startup sync immediately).

- [ ] **Step 5: Commit**

```bash
cd ~/projects/LeagueDeck
git add frontend
git commit -m "Scaffold frontend: routing, API client, unstyled functional pages"
```

---

## Task 10: Frontend visual design gate

This task has no predetermined code — its entire point is that the visual direction is chosen interactively, not decided in advance by whoever implements this plan. Do not skip ahead and style the pages before this task runs.

**Files:**
- Modify: `frontend/tailwind.config.js` (or `frontend/src/index.css` `@plugin "daisyui"` config, depending on daisyUI version) — theme tokens for the chosen direction.
- Modify: `frontend/src/pages/LoginPage.jsx`, `frontend/src/pages/SettingsPage.jsx`, `frontend/src/pages/DashboardPage.jsx`, `frontend/src/App.jsx` — apply the chosen direction's classes. Logic/behavior from Task 9 does not change, only markup/classes.

- [ ] **Step 1: Invoke the `frontend-design` skill for guidance on generating genuinely distinct directions** (not three variations on the same idea).

- [ ] **Step 2: Produce at least 3 visually distinct design directions** for the Login and Dashboard screens (e.g. as static HTML/CSS comparison mockups, or daisyUI theme previews) — distinct in theme/color, typography, and layout density, not just accent-color swaps.

- [ ] **Step 3: Present the options to the user and get an explicit selection before writing any component code.** Use the `Artifact` tool to render the mockups side by side if that communicates the differences better than describing them in text, or `AskUserQuestion` if a straightforward multiple-choice pick is enough.

- [ ] **Step 4: Apply the chosen direction** to `tailwind.config.js`/daisyUI theme config and restyle `LoginPage.jsx`, `SettingsPage.jsx`, `DashboardPage.jsx`, and the `App.jsx` nav with the chosen classes/components.

- [ ] **Step 5: Manually verify** — `npm run dev`, click through `/login`, `/settings`, `/` and confirm the chosen direction is applied consistently across all three screens.

- [ ] **Step 6: Commit**

```bash
cd ~/projects/LeagueDeck
git add frontend
git commit -m "Apply chosen visual design direction to frontend"
```

---

## Task 11: Docker packaging and end-to-end verification

**Files:**
- Create: `backend/Dockerfile`
- Create: `frontend/Dockerfile`
- Create: `frontend/nginx.conf`
- Create: `docker-compose.yml`
- Modify: `.env.example`
- Create: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1–10.
- Produces: a `docker-compose up` deployment — the deliverable this whole plan is building toward.

- [ ] **Step 1: Write the backend Dockerfile**

`backend/Dockerfile`:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN mkdir -p /app/data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Write the frontend Dockerfile and nginx config**

`frontend/Dockerfile`:

```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

`frontend/nginx.conf`:

```nginx
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

- [ ] **Step 3: Write docker-compose.yml**

`docker-compose.yml` (repo root):

```yaml
services:
  backend:
    build:
      context: ./backend
    ports:
      - "8000:8000"
    env_file:
      - .env
    volumes:
      - leaguedeck-data:/app/data
    restart: unless-stopped

  frontend:
    build:
      context: ./frontend
    ports:
      - "5173:80"
    depends_on:
      - backend
    restart: unless-stopped

volumes:
  leaguedeck-data:
```

- [ ] **Step 4: Update `.env.example` with the containerized DB path**

`.env.example`:

```
DATABASE_URL=sqlite:////app/data/leaguedeck.db
APP_PASSWORD_HASH=
SECRET_KEY=
FERNET_KEY=
ENABLE_SCHEDULER=true
```

- [ ] **Step 5: Write the README quick-start**

`README.md`:

````markdown
# LeagueDeck

Self-hosted dashboard unifying fantasy football leagues across Sleeper,
ESPN, and Yahoo. See `docs/superpowers/specs/2026-09-07-leaguedeck-design.md`
for the full design.

This is the Phase 1a build: foundation + Sleeper only (read-only). ESPN,
Yahoo, and the injury-alerts/start-sit/waiver-wire features are follow-on
plans in `docs/superpowers/plans/`.

## Quick start

1. Copy `.env.example` to `.env`.
2. Generate the three required secrets:

   ```bash
   python3 -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash(input('App password: ')))"
   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Paste the three outputs into `.env` as `APP_PASSWORD_HASH`, `SECRET_KEY`,
   and `FERNET_KEY` respectively.

3. `docker-compose up --build`
4. Open `http://localhost:5173`, log in with the password you hashed above,
   go to Settings, enter your Sleeper username and league ID(s), then go to
   the dashboard.
````

- [ ] **Step 6: Verify the full stack boots and works**

```bash
cd ~/projects/LeagueDeck
docker-compose up --build
```

In another terminal, confirm the backend is reachable:

```bash
curl http://localhost:8000/health
```

Expected: `{"status":"ok"}`

Then open `http://localhost:5173` in a browser, log in, configure Sleeper settings with a real username/league ID, and confirm the dashboard renders real roster/matchup data.

- [ ] **Step 7: Commit**

```bash
git add backend/Dockerfile frontend/Dockerfile frontend/nginx.conf docker-compose.yml .env.example README.md
git commit -m "Add Docker packaging and quick-start README"
```

---

## What's next (not in this plan)

- **ESPN adapter plan**: manual cookie settings UI, `espn.py` adapter, encrypted `Secret` storage (crypto.py already built), and the ffverse/nflverse player-ID crosswalk (now needed since there are two platforms to reconcile).
- **Yahoo adapter plan**: OAuth2 consent flow, HTTPS reverse proxy for the callback, `yahoo.py` adapter.
- **Dashboard features 2–4 plan(s)**: injury/news alerts, start/sit recommendations (needs a weekly-projections source per the spec), waiver wire/trending pickups.
- **Phase 2 plan(s)**: lineup write-back — Yahoo official API first, then ESPN direct-POST/Playwright, then Sleeper Playwright + its own manually-captured session cookie.
