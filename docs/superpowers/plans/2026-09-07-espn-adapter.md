# ESPN Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add ESPN as LeagueDeck's second platform — settings for ESPN cookies/league IDs, a sync job that normalizes ESPN leagues into the same `League`/`Team` rows Sleeper already produces, and the frontend fixes needed so ESPN leagues render correctly on the existing dashboard.

**Architecture:** A new `app/adapters/espn.py` calls ESPN's private v3 API (combined `mTeam`/`mRoster`/`mMatchup`/`mSettings` view, one HTTP call per league) and normalizes the response into the exact shape `sleeper.normalize_league` already produces — this is why `sync.py`'s per-league commit loop, `app/routers/leagues.py`, and most of the frontend need no changes to consume ESPN data once it exists as rows. A new `sync_espn(db)` in `app/sync.py` mirrors `sync_sleeper`'s per-league try/except isolation, but skips creating any `SyncLog` row at all when ESPN isn't configured (Sleeper's "unconfigured" state is brief; ESPN's may be permanent for many users). Settings gets a new `GET/PUT /settings/espn` with an optional-secret PUT contract so a league-IDs-only edit can't blank out stored cookies. Two required frontend fixes ship alongside: a new ESPN section on the Settings page, and gating the Waiver Wire section to Sleeper-only leagues (it 404s for any other platform today, which currently reads as "no players trending" instead of "not supported here").

**Tech Stack:** Same as the rest of the project — FastAPI/SQLAlchemy backend (`httpx` for the ESPN call, `cryptography.fernet` via the existing `app/crypto.py` for the stored cookies), React frontend, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-07-espn-adapter-design.md` (reviewed and revised — read it for the full rationale behind each decision below, especially the numeric ID mapping tables and the optional-secret PUT contract).

## Global Constraints

- No player-ID crosswalk — ESPN resolves names/positions/teams from its own response only; no cross-platform player matching (spec's Scope decisions).
- Per-player object contract, matching Sleeper's exactly: `{"player_id": str, "name": str, "position": str | None, "team": str | None, "is_starter": bool}`.
- League-level output contract, matching Sleeper's exactly: `{"platform", "platform_league_id", "name", "season": str, "teams": [...]}` with each team having `platform_team_id, name, is_mine, roster_json, points_for, opponent_name, opponent_points, week`.
- Position mapping (`defaultPositionId` → string): `1: "QB"`, `2: "RB"`, `3: "WR"`, `4: "TE"`, `5: "K"`, `16: "DEF"` — ESPN's defense slot MUST map to `"DEF"`, never `"D/ST"`, or the frontend's `POSITION_ORDER` array silently mis-sorts it.
- Pro-team mapping (`proTeamId` → abbreviation): static 32-team table, `0` maps to `None` (matching Sleeper's null convention for unrostered/no-team, not the string `"FA"`).
- Starter vs. bench (`is_starter`): derived from `lineupSlotId` — bench is `20`, IR is `21`; every other slot ID counts as a starter.
- `is_mine` is determined by normalizing and comparing GUIDs (strip braces, uppercase) between the stored `swid` and each team's `owners` list — never a naive string `==`.
- `season` is stored as `str` on the `League` row (matching Sleeper's convention and the column's actual type) — the adapter accepts `season: int` but returns `str(season)`.
- `PUT /settings/espn`: `espn_s2` and `swid` are optional; omitted or blank means "leave the currently stored value unchanged" — only a non-blank value overwrites. `league_ids` always overwrites (same rule as Sleeper's).
- `GET /settings/espn` never echoes `espn_s2`/`swid` — it returns `espn_s2_configured`/`swid_configured` booleans instead.
- `sync_espn(db)` must write **zero** `SyncLog` rows when ESPN isn't configured (no league IDs, or either secret missing) — this is what keeps `/sync-status` from reporting a permanently-degraded platform nobody set up.
- Per-league isolation in `sync_espn`, mirroring `sync_sleeper`: one bad ESPN league must not block any other ESPN league or Sleeper.
- Waiver Wire fetch/render on the dashboard must be gated to `league.platform === 'sleeper'` — this is a required correctness fix, not optional polish.

---

## Task 1: ESPN adapter — ID mapping tables and `normalize_league`

**Files:**
- Create: `backend/app/adapters/espn.py`
- Create: `backend/tests/test_espn_adapter.py`

**Interfaces:**
- Produces: `espn.normalize_league(league_id: str, season: int, espn_s2: str, swid: str) -> dict`, returning the shared League/Team shape described in Global Constraints. Task 3's `sync.py` calls this directly by name. Also produces `espn.EspnAdapterError` (raised nowhere yet in this task, but the exception class other tasks' tests reference to simulate a failed league).
- Consumes: nothing from other tasks (uses only `httpx`, stdlib).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_espn_adapter.py`:

```python
import httpx

from app.adapters import espn


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def _combined_response(**overrides):
    """A minimal but representative combined mTeam/mRoster/mMatchup/mSettings
    response: two teams, one roster entry each, one scored matchup between them."""
    base = {
        "scoringPeriodId": 3,
        "settings": {"name": "Test League"},
        "teams": [
            {
                "id": 1,
                "location": "Team",
                "nickname": "One",
                "owners": ["{ABC-123}"],
                "roster": {
                    "entries": [
                        {
                            "lineupSlotId": 0,
                            "playerPoolEntry": {
                                "player": {
                                    "id": 111,
                                    "fullName": "Player One",
                                    "defaultPositionId": 2,
                                    "proTeamId": 12,
                                }
                            },
                        },
                        {
                            "lineupSlotId": 20,
                            "playerPoolEntry": {
                                "player": {
                                    "id": 222,
                                    "fullName": "Bench Guy",
                                    "defaultPositionId": 3,
                                    "proTeamId": 0,
                                }
                            },
                        },
                    ]
                },
            },
            {
                "id": 2,
                "location": "Team",
                "nickname": "Two",
                "owners": ["{XYZ-999}"],
                "roster": {"entries": []},
            },
        ],
        "schedule": [
            {
                "matchupPeriodId": 3,
                "home": {"teamId": 1, "totalPoints": 100.5},
                "away": {"teamId": 2, "totalPoints": 90.2},
            }
        ],
    }
    base.update(overrides)
    return base


def test_normalize_league_builds_teams_with_mapped_position_and_team(monkeypatch):
    def fake_get(url, params=None, cookies=None, timeout=10.0):
        assert url == f"{espn.ESPN_BASE_URL}/2026/segments/0/leagues/999"
        assert cookies == {"espn_s2": "s2val", "SWID": "{ABC-123}"}
        assert ("view", "mTeam") in params
        assert ("view", "mRoster") in params
        assert ("view", "mMatchup") in params
        assert ("view", "mSettings") in params
        return FakeResponse(_combined_response())

    monkeypatch.setattr(httpx, "get", fake_get)

    result = espn.normalize_league("999", 2026, "s2val", "{ABC-123}")

    assert result["platform"] == "espn"
    assert result["platform_league_id"] == "999"
    assert result["name"] == "Test League"
    assert result["season"] == "2026"  # cast to str even though 2026 (int) was passed in

    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["platform_team_id"] == "1"
    assert my_team["name"] == "Team One"
    assert my_team["points_for"] == 100.5
    assert my_team["opponent_name"] == "Team Two"
    assert my_team["opponent_points"] == 90.2
    assert my_team["week"] == 3

    starter = next(p for p in my_team["roster_json"] if p["player_id"] == "111")
    assert starter == {
        "player_id": "111",
        "name": "Player One",
        "position": "RB",
        "team": "KC",
        "is_starter": True,
    }
    bench = next(p for p in my_team["roster_json"] if p["player_id"] == "222")
    assert bench["position"] == "WR"
    assert bench["team"] is None  # proTeamId 0 (free agent) maps to None, not "FA"
    assert bench["is_starter"] is False  # lineupSlotId 20 == bench


def test_normalize_league_maps_defense_slot_to_def_not_dst(monkeypatch):
    """ESPN's own label for this position is 'D/ST'. The frontend's
    POSITION_ORDER array is Sleeper's vocabulary and only recognizes 'DEF' --
    an unmapped 'D/ST' would silently sort to the bottom of every roster."""
    response = _combined_response()
    response["teams"][0]["roster"]["entries"] = [
        {
            "lineupSlotId": 16,
            "playerPoolEntry": {
                "player": {
                    "id": 333,
                    "fullName": "Chiefs D/ST",
                    "defaultPositionId": 16,
                    "proTeamId": 12,
                }
            },
        }
    ]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(response))

    result = espn.normalize_league("999", 2026, "s2val", "{ABC-123}")
    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["roster_json"][0]["position"] == "DEF"


def test_normalize_league_excludes_ir_slot_from_starters(monkeypatch):
    response = _combined_response()
    response["teams"][0]["roster"]["entries"] = [
        {
            "lineupSlotId": 21,  # IR
            "playerPoolEntry": {
                "player": {
                    "id": 444,
                    "fullName": "Hurt Guy",
                    "defaultPositionId": 1,
                    "proTeamId": 7,
                }
            },
        }
    ]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(response))

    result = espn.normalize_league("999", 2026, "s2val", "{ABC-123}")
    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["roster_json"][0]["is_starter"] is False


def test_normalize_league_matches_swid_case_and_brace_insensitively(monkeypatch):
    """A user pasting from DevTools may omit braces or differ in case from
    what ESPN echoes back in `owners` -- the comparison must not be a naive =="""
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(_combined_response()))

    result = espn.normalize_league("999", 2026, "s2val", "abc-123")  # no braces, lowercase
    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["platform_team_id"] == "1"


def test_normalize_league_prefers_explicit_name_over_location_nickname(monkeypatch):
    response = _combined_response()
    response["teams"][0]["name"] = "Explicit Team Name"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(response))

    result = espn.normalize_league("999", 2026, "s2val", "{ABC-123}")
    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["name"] == "Explicit Team Name"


def test_normalize_league_handles_team_with_no_scheduled_game(monkeypatch):
    """An odd-team league (or a bye week) can leave a team with no schedule
    entry at all for the current week -- own points default to 0.0, no
    opponent, matching Sleeper's convention for the same situation."""
    response = _combined_response()
    response["schedule"] = []
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(response))

    result = espn.normalize_league("999", 2026, "s2val", "{ABC-123}")
    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["points_for"] == 0.0
    assert my_team["opponent_name"] is None
    assert my_team["opponent_points"] is None


def test_normalize_league_falls_back_to_player_id_when_name_missing(monkeypatch):
    response = _combined_response()
    response["teams"][0]["roster"]["entries"] = [
        {
            "lineupSlotId": 0,
            "playerPoolEntry": {
                "player": {"id": 555, "defaultPositionId": 5, "proTeamId": 99}
            },
        }
    ]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(response))

    result = espn.normalize_league("999", 2026, "s2val", "{ABC-123}")
    my_team = next(t for t in result["teams"] if t["is_mine"])
    player = my_team["roster_json"][0]
    assert player["name"] == "555"
    assert player["position"] == "K"
    assert player["team"] is None  # unknown proTeamId (99) also falls back to None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python3 -m pytest tests/test_espn_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.adapters.espn'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/adapters/espn.py`:

```python
import httpx

ESPN_BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons"

BENCH_SLOT_ID = 20
IR_SLOT_ID = 21

# defaultPositionId -> the string vocabulary the rest of this app already
# uses (Sleeper normalizes to the same strings). ESPN's own label for 16 is
# "D/ST" -- it must map to "DEF" here, not carried through verbatim, or the
# frontend's POSITION_ORDER array (Sleeper's vocabulary) won't recognize it.
POSITION_MAP = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF"}

# proTeamId -> team abbreviation, matching the 2-3 letter convention Sleeper's
# `team` field already uses. IDs are not contiguous (31/32 unused; 33/34 are
# later-added expansion teams) -- this is ESPN's actual numbering, not a typo.
# 0 (free agent / no team) maps to None, matching Sleeper's null convention.
PRO_TEAM_MAP = {
    0: None,
    1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN",
    8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR",
    15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ",
    21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA",
    27: "TB", 28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}


class EspnAdapterError(Exception):
    pass


def _normalize_guid(raw: str) -> str:
    """ESPN GUIDs are brace-wrapped and case can differ between what a user
    pastes from DevTools and what the API echoes back in `owners` -- strip
    braces and uppercase before comparing so a cosmetic mismatch doesn't
    misidentify (or fail to identify) the user's own team."""
    return raw.strip().strip("{}").upper()


def _get_combined_view(league_id: str, season: int, espn_s2: str, swid: str) -> dict:
    url = f"{ESPN_BASE_URL}/{season}/segments/0/leagues/{league_id}"
    resp = httpx.get(
        url,
        params=[
            ("view", "mTeam"),
            ("view", "mRoster"),
            ("view", "mMatchup"),
            ("view", "mSettings"),
        ],
        cookies={"espn_s2": espn_s2, "SWID": swid},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


def _team_display_name(team: dict) -> str:
    name = (team.get("name") or "").strip()
    if name:
        return name
    return f"{team.get('location', '')} {team.get('nickname', '')}".strip()


def _team_score(schedule: list[dict], week: int, team_id: int):
    """Returns (points_for, opponent_team_id, opponent_points) for one team in
    one week, or (0.0, None, None) if the team has no scheduled game that week
    (bye week in an odd-team league) -- matching Sleeper's convention of
    reporting 0.0 rather than None for a team's own missing score."""
    for game in schedule:
        if game.get("matchupPeriodId") != week:
            continue
        home, away = game.get("home") or {}, game.get("away") or {}
        if home.get("teamId") == team_id:
            return home.get("totalPoints", 0.0), away.get("teamId"), away.get("totalPoints")
        if away.get("teamId") == team_id:
            return away.get("totalPoints", 0.0), home.get("teamId"), home.get("totalPoints")
    return 0.0, None, None


def normalize_league(league_id: str, season: int, espn_s2: str, swid: str) -> dict:
    """Fetch everything for one ESPN league and normalize into LeagueDeck's
    shared League/Team shape (the same shape sleeper.normalize_league produces)."""
    data = _get_combined_view(league_id, season, espn_s2, swid)

    week = data["scoringPeriodId"]
    schedule = data.get("schedule") or []
    my_guid = _normalize_guid(swid)

    teams_by_id = {t["id"]: t for t in data.get("teams") or []}

    teams = []
    for team in teams_by_id.values():
        points_for, opponent_team_id, opponent_points = _team_score(schedule, week, team["id"])
        opponent = teams_by_id.get(opponent_team_id)
        opponent_name = _team_display_name(opponent) if opponent else None

        owners = team.get("owners") or []
        is_mine = any(_normalize_guid(o) == my_guid for o in owners)

        entries = ((team.get("roster") or {}).get("entries")) or []
        roster_players = []
        for entry in entries:
            player = (entry.get("playerPoolEntry") or {}).get("player") or {}
            slot_id = entry.get("lineupSlotId")
            player_id = str(player.get("id"))
            roster_players.append(
                {
                    "player_id": player_id,
                    "name": player.get("fullName") or player_id,
                    "position": POSITION_MAP.get(player.get("defaultPositionId")),
                    "team": PRO_TEAM_MAP.get(player.get("proTeamId")),
                    "is_starter": slot_id not in (BENCH_SLOT_ID, IR_SLOT_ID),
                }
            )

        teams.append(
            {
                "platform_team_id": str(team["id"]),
                "name": _team_display_name(team),
                "is_mine": is_mine,
                "roster_json": roster_players,
                "points_for": points_for,
                "opponent_name": opponent_name,
                "opponent_points": opponent_points,
                "week": week,
            }
        )

    return {
        "platform": "espn",
        "platform_league_id": league_id,
        "name": data.get("settings", {}).get("name") or f"ESPN League {league_id}",
        "season": str(season),
        "teams": teams,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_espn_adapter.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the full backend suite**

Run: `python3 -m pytest -q`
Expected: All tests pass (no other file references `app.adapters.espn` yet)

- [ ] **Step 6: Commit**

```bash
git add backend/app/adapters/espn.py backend/tests/test_espn_adapter.py
git commit -m "Add ESPN adapter: normalize_league with numeric ID mapping"
```

---

## Task 2: Settings — `GET/PUT /settings/espn`

**Files:**
- Modify: `backend/app/routers/settings.py`
- Modify: `backend/tests/test_settings_router.py`

**Interfaces:**
- Consumes: `app.crypto.{encrypt_value, decrypt_value}` (existing), `app.models.{Secret, AppSetting}` (existing, `Secret` unused until now).
- Produces: `GET /settings/espn` → `{"league_ids": list[str], "espn_s2_configured": bool, "swid_configured": bool}`. `PUT /settings/espn` accepting `{"espn_s2": str | None, "swid": str | None, "league_ids": list[str]}`. Task 3's `sync_espn` reads the same `AppSetting` key (`espn_league_ids`) and `Secret` keys (`espn_s2`, `espn_swid`) this task writes — those three key names are load-bearing for Task 3.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_settings_router.py`:

```python
def test_espn_settings_requires_auth(client):
    resp = client.get("/settings/espn")
    assert resp.status_code == 401


def test_put_and_get_espn_settings_roundtrip(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})

    put_resp = client.put(
        "/settings/espn",
        json={"espn_s2": "s2secret", "swid": "{GUID}", "league_ids": ["111", "222"]},
    )
    assert put_resp.status_code == 200

    get_resp = client.get("/settings/espn")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["league_ids"] == ["111", "222"]
    assert data["espn_s2_configured"] is True
    assert data["swid_configured"] is True
    # Must never echo the raw secret values anywhere in the response body --
    # not just under the expected field names.
    assert "s2secret" not in get_resp.text
    assert "{GUID}" not in get_resp.text


def test_get_espn_settings_before_any_put_reports_unconfigured(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})

    resp = client.get("/settings/espn")
    assert resp.status_code == 200
    assert resp.json() == {
        "league_ids": [],
        "espn_s2_configured": False,
        "swid_configured": False,
    }


def test_put_espn_settings_with_league_ids_only_preserves_existing_secrets(client):
    """Omitting the cookie fields entirely on a later PUT (e.g. a
    league-IDs-only edit) must not wipe the already-stored secrets."""
    client.post("/auth/login", json={"password": TEST_PASSWORD})
    client.put(
        "/settings/espn",
        json={"espn_s2": "s2secret", "swid": "{GUID}", "league_ids": ["111"]},
    )

    update_resp = client.put("/settings/espn", json={"league_ids": ["111", "222"]})
    assert update_resp.status_code == 200

    data = client.get("/settings/espn").json()
    assert data["league_ids"] == ["111", "222"]
    assert data["espn_s2_configured"] is True
    assert data["swid_configured"] is True


def test_put_espn_settings_with_blank_cookie_strings_preserves_existing_secrets(client):
    """Blank strings must be treated the same as omitted fields -- 'leave
    unchanged' -- not as an explicit request to overwrite with blank."""
    client.post("/auth/login", json={"password": TEST_PASSWORD})
    client.put(
        "/settings/espn",
        json={"espn_s2": "s2secret", "swid": "{GUID}", "league_ids": ["111"]},
    )

    client.put(
        "/settings/espn",
        json={"espn_s2": "", "swid": "", "league_ids": ["111"]},
    )

    data = client.get("/settings/espn").json()
    assert data["espn_s2_configured"] is True
    assert data["swid_configured"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_settings_router.py -k espn -v`
Expected: FAIL with 404s (no `/settings/espn` route yet)

- [ ] **Step 3: Write the implementation**

In `backend/app/routers/settings.py`, update the imports and add the new model + routes:

```python
from app.auth import require_auth
from app.crypto import decrypt_value, encrypt_value
from app.db import get_db
from app.models import AppSetting, Secret
```

Add after `SleeperSettings`:

```python
class EspnSettings(BaseModel):
    espn_s2: str | None = None
    swid: str | None = None
    league_ids: list[str]
```

Add after `_get_setting`:

```python
def _set_secret(db: Session, key: str, value: str) -> None:
    row = db.query(Secret).filter(Secret.key == key).first()
    encrypted = encrypt_value(value)
    if row is None:
        db.add(Secret(key=key, encrypted_value=encrypted))
    else:
        row.encrypted_value = encrypted
    db.commit()


def _secret_configured(db: Session, key: str) -> bool:
    return db.query(Secret).filter(Secret.key == key).first() is not None
```

Add after `set_sleeper_settings`:

```python
@router.get("/espn")
def get_espn_settings(db: Session = Depends(get_db)):
    league_ids_raw = _get_setting(db, "espn_league_ids") or ""
    return {
        "league_ids": [x.strip() for x in league_ids_raw.split(",") if x.strip()],
        "espn_s2_configured": _secret_configured(db, "espn_s2"),
        "swid_configured": _secret_configured(db, "espn_swid"),
    }


@router.put("/espn")
def set_espn_settings(payload: EspnSettings, db: Session = Depends(get_db)):
    # espn_s2/swid are optional: omitted or blank means "leave unchanged".
    # A naive always-overwrite here would let a league-IDs-only edit (the
    # common case, since GET never echoes the real cookie values back into
    # the form) silently wipe already-stored credentials.
    if payload.espn_s2 and payload.espn_s2.strip():
        _set_secret(db, "espn_s2", payload.espn_s2.strip())
    if payload.swid and payload.swid.strip():
        _set_secret(db, "espn_swid", payload.swid.strip())
    _set_setting(
        db,
        "espn_league_ids",
        ",".join(x.strip() for x in payload.league_ids if x.strip()),
    )
    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_settings_router.py -v`
Expected: PASS (all Sleeper tests still pass, all new ESPN tests pass)

- [ ] **Step 5: Run the full backend suite**

Run: `python3 -m pytest -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/settings.py backend/tests/test_settings_router.py
git commit -m "Add GET/PUT /settings/espn with optional-secret PUT contract"
```

---

## Task 3: Sync — `sync_espn` and wiring into `sync_all_platforms`

**Files:**
- Modify: `backend/app/sync.py`
- Create: `backend/tests/test_espn_sync.py`

**Interfaces:**
- Consumes: `espn.normalize_league(league_id, season, espn_s2, swid)` (Task 1), `app.crypto.decrypt_value` (existing), `app.models.Secret` (existing), the `AppSetting` key `espn_league_ids` and `Secret` keys `espn_s2`/`espn_swid` (Task 2 writes these).
- Produces: `sync_espn(db: Session) -> None`, called from `sync_all_platforms()` alongside `sync_sleeper(db)`. Also produces `_current_espn_season(now: datetime | None = None) -> int` (module-private, but this task's own tests exercise it directly).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_espn_sync.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_espn_sync.py -v`
Expected: FAIL with `ImportError: cannot import name '_current_espn_season'` (or `sync_espn`)

- [ ] **Step 3: Write the implementation**

In `backend/app/sync.py`, update imports:

```python
from app.adapters import espn as espn_adapter
from app.adapters import sleeper as sleeper_adapter
from app.crypto import decrypt_value
from app.db import get_sessionmaker
from app.models import AppSetting, League, Secret, SyncLog, Team
```

Add after `_get_setting`:

```python
def _get_secret(db: Session, key: str) -> str | None:
    row = db.query(Secret).filter(Secret.key == key).first()
    return row.encrypted_value if row else None
```

Add after `_sync_one_league` (renaming nothing — this is a new, separate function per the spec's "structurally parallel, not shared code" decision):

```python
def _current_espn_season(now: datetime | None = None) -> int:
    """ESPN league IDs persist across seasons; season is a separate URL
    parameter the adapter needs. NFL seasons span roughly September-February,
    so a January/February sync should still target the season that started
    the previous calendar year."""
    now = now or datetime.now(timezone.utc)
    return now.year - 1 if now.month < 3 else now.year


def _sync_one_espn_league(
    db: Session, league_id: str, season: int, espn_s2: str, swid: str
) -> None:
    """Refresh a single ESPN league. Caller commits/rolls back so that one
    league's failure cannot discard another league's work."""
    normalized = espn_adapter.normalize_league(league_id, season, espn_s2, swid)

    league = (
        db.query(League)
        .filter(League.platform == "espn", League.platform_league_id == league_id)
        .first()
    )
    if league is None:
        league = League(
            platform="espn",
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


def sync_espn(db: Session) -> None:
    """ESPN setup is genuinely optional -- many users may never configure it.
    Unlike sync_sleeper, this checks configuration *before* creating any
    SyncLog row: if unconfigured, /sync-status should simply omit "espn" from
    its platform list rather than report a permanently-degraded platform."""
    league_ids = _parse_league_ids(_get_setting(db, "espn_league_ids") or "")
    espn_s2_encrypted = _get_secret(db, "espn_s2")
    swid_encrypted = _get_secret(db, "espn_swid")
    if not league_ids or not espn_s2_encrypted or not swid_encrypted:
        return

    log = SyncLog(platform="espn", started_at=datetime.now(timezone.utc))
    db.add(log)
    db.commit()

    try:
        espn_s2 = decrypt_value(espn_s2_encrypted)
        swid = decrypt_value(swid_encrypted)
        season = _current_espn_season()

        errors: list[str] = []
        for league_id in league_ids:
            try:
                _sync_one_espn_league(db, league_id, season, espn_s2, swid)
                db.commit()
            except Exception as exc:
                _safe_rollback(db)
                logger.exception("espn sync: league %s failed", league_id)
                errors.append(f"league {league_id}: {exc}")

        log.success = not errors
        log.error = "; ".join(errors) if errors else None
    except Exception as exc:  # sync must never crash the scheduler
        _safe_rollback(db)
        log.success = False
        log.error = str(exc)
    finally:
        try:
            log.finished_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            logger.exception("espn sync: failed to record SyncLog completion")
            _safe_rollback(db)
```

Update `sync_all_platforms`:

```python
def sync_all_platforms() -> None:
    db = get_sessionmaker()()
    try:
        sync_sleeper(db)
        sync_espn(db)
        # Yahoo adapter is added by its own follow-on plan.
    finally:
        db.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_espn_sync.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the full backend suite**

Run: `python3 -m pytest -q`
Expected: All tests pass, including all existing Sleeper sync tests unchanged

- [ ] **Step 6: Commit**

```bash
git add backend/app/sync.py backend/tests/test_espn_sync.py
git commit -m "Add sync_espn with skip-when-unconfigured behavior"
```

---

## Task 4: Frontend — ESPN settings section

**Files:**
- Modify: `frontend/src/api/client.js`
- Modify: `frontend/src/pages/SettingsPage.jsx`

**Interfaces:**
- Consumes: `GET/PUT /settings/espn` (Task 2).
- Produces: `getEspnSettings()`, `putEspnSettings(leagueIds, espnS2, swid)` in `api/client.js`, used only by `SettingsPage.jsx`.

- [ ] **Step 1: Add API client functions**

In `frontend/src/api/client.js`, add after `putSleeperSettings`:

```js
export function getEspnSettings() {
  return request('/settings/espn')
}

export function putEspnSettings(leagueIds, espnS2, swid) {
  const body = { league_ids: leagueIds }
  // Only send the cookie fields if the user actually typed something this
  // time -- the backend's optional-secret PUT contract treats a present-
  // but-blank field the same as an overwrite, so a league-IDs-only edit
  // must omit them entirely to avoid wiping stored credentials.
  if (espnS2) body.espn_s2 = espnS2
  if (swid) body.swid = swid
  return request('/settings/espn', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}
```

- [ ] **Step 2: Add the ESPN section to SettingsPage**

Replace the full contents of `frontend/src/pages/SettingsPage.jsx`:

```jsx
import { useEffect, useState } from 'react'
import {
  getEspnSettings,
  getSleeperSettings,
  putEspnSettings,
  putSleeperSettings,
} from '../api/client'

export default function SettingsPage() {
  const [username, setUsername] = useState('')
  const [leagueIdsText, setLeagueIdsText] = useState('')
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)

  const [espnS2, setEspnS2] = useState('')
  const [swid, setSwid] = useState('')
  const [espnLeagueIdsText, setEspnLeagueIdsText] = useState('')
  const [espnS2Configured, setEspnS2Configured] = useState(false)
  const [swidConfigured, setSwidConfigured] = useState(false)
  const [espnSaved, setEspnSaved] = useState(false)
  const [espnError, setEspnError] = useState(null)

  useEffect(() => {
    getSleeperSettings()
      .then((data) => {
        setUsername(data.username)
        setLeagueIdsText(data.league_ids.join(', '))
      })
      .catch((err) => {
        setError(
          err.status === 401
            ? 'Your session expired. Log in again to load settings.'
            : 'Could not load settings.',
        )
      })

    getEspnSettings()
      .then((data) => {
        setEspnLeagueIdsText(data.league_ids.join(', '))
        setEspnS2Configured(data.espn_s2_configured)
        setSwidConfigured(data.swid_configured)
      })
      .catch((err) => {
        setEspnError(
          err.status === 401
            ? 'Your session expired. Log in again to load settings.'
            : 'Could not load settings.',
        )
      })
  }, [])

  async function handleSubmit(e) {
    e.preventDefault()
    const leagueIds = leagueIdsText
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    setError(null)
    setSaved(false)
    try {
      await putSleeperSettings(username, leagueIds)
      setSaved(true)
    } catch (err) {
      setError(
        err.status === 401
          ? 'Your session expired. Log in again to save settings.'
          : 'Could not save settings.',
      )
    }
  }

  async function handleEspnSubmit(e) {
    e.preventDefault()
    const leagueIds = espnLeagueIdsText
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    setEspnError(null)
    setEspnSaved(false)
    try {
      await putEspnSettings(leagueIds, espnS2, swid)
      setEspnSaved(true)
      // Cookie fields are never echoed back by GET -- clear them from the
      // form and rely on the "configured" labels to show they're stored.
      setEspnS2('')
      setSwid('')
      const data = await getEspnSettings()
      setEspnS2Configured(data.espn_s2_configured)
      setSwidConfigured(data.swid_configured)
    } catch (err) {
      setEspnError(
        err.status === 401
          ? 'Your session expired. Log in again to save settings.'
          : 'Could not save settings.',
      )
    }
  }

  return (
    <div className="ld-page">
      <div className="ld-cover-band">
        <h1 className="ld-title">LeagueDeck</h1>
        <p className="ld-sub">settings</p>
      </div>
      <form className="ld-settings-form" onSubmit={handleSubmit}>
        <label htmlFor="username">Sleeper username</label>
        <input
          id="username"
          className="ld-input"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
        <label htmlFor="leagueIds">Sleeper league IDs (comma-separated)</label>
        <input
          id="leagueIds"
          className="ld-input"
          value={leagueIdsText}
          onChange={(e) => setLeagueIdsText(e.target.value)}
        />
        <button type="submit" className="ld-button">
          Save
        </button>
        {saved && <p className="ld-saved">Saved.</p>}
        {error && (
          <p role="alert" className="ld-error">
            {error}
          </p>
        )}
      </form>

      <form className="ld-settings-form" onSubmit={handleEspnSubmit}>
        <label htmlFor="espnS2">
          ESPN espn_s2 cookie {espnS2Configured && '(already saved — leave blank to keep it)'}
        </label>
        <input
          id="espnS2"
          type="password"
          className="ld-input"
          value={espnS2}
          onChange={(e) => setEspnS2(e.target.value)}
        />
        <label htmlFor="swid">
          ESPN SWID cookie {swidConfigured && '(already saved — leave blank to keep it)'}
        </label>
        <input
          id="swid"
          type="password"
          className="ld-input"
          value={swid}
          onChange={(e) => setSwid(e.target.value)}
        />
        <label htmlFor="espnLeagueIds">ESPN league IDs (comma-separated)</label>
        <input
          id="espnLeagueIds"
          className="ld-input"
          value={espnLeagueIdsText}
          onChange={(e) => setEspnLeagueIdsText(e.target.value)}
        />
        <button type="submit" className="ld-button">
          Save
        </button>
        {espnSaved && <p className="ld-saved">Saved.</p>}
        {espnError && (
          <p role="alert" className="ld-error">
            {espnError}
          </p>
        )}
      </form>
    </div>
  )
}
```

- [ ] **Step 3: Lint**

Run: `cd frontend && npm run lint`
Expected: No errors

- [ ] **Step 4: Manual verification**

Run: `npm run dev` (from `frontend/`, with the backend running per the project's normal dev setup)
Open the Settings page in a browser and confirm:
- The ESPN section renders below the Sleeper section with its own Save button.
- Saving ESPN league IDs only (leaving the cookie fields blank) after previously saving cookies keeps showing "(already saved — leave blank to keep it)" for both cookie fields — confirms the frontend never sent blank overwrites.
- Saving with real cookie values succeeds and the fields clear back to blank with the "already saved" labels showing.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.js frontend/src/pages/SettingsPage.jsx
git commit -m "Add ESPN section to Settings page"
```

---

## Task 5: Frontend — gate Waiver Wire to Sleeper leagues

**Files:**
- Modify: `frontend/src/pages/DashboardPage.jsx`

**Interfaces:**
- Consumes: `league.platform` (already present on every league object returned by `GET /leagues`, no backend change needed).

- [ ] **Step 1: Gate the fetch**

In `frontend/src/pages/DashboardPage.jsx`, in `loadData`, change:

```js
        data.forEach((league) => {
          getWaiverWire(league.id)
            .then((players) => setWaiverWire((prev) => ({ ...prev, [league.id]: players })))
            .catch(() => setWaiverWire((prev) => ({ ...prev, [league.id]: [] })))
        })
```

to:

```js
        data.forEach((league) => {
          if (league.platform !== 'sleeper') return
          getWaiverWire(league.id)
            .then((players) => setWaiverWire((prev) => ({ ...prev, [league.id]: players })))
            .catch(() => setWaiverWire((prev) => ({ ...prev, [league.id]: [] })))
        })
```

- [ ] **Step 2: Gate the render**

In the same file, change:

```jsx
                <div className="ld-lineup-section">
                  <h3 className="ld-lineup-label">Waiver wire</h3>
                  {waiverWire[league.id] === undefined ? (
```

to:

```jsx
                {league.platform === 'sleeper' && (
                <div className="ld-lineup-section">
                  <h3 className="ld-lineup-label">Waiver wire</h3>
                  {waiverWire[league.id] === undefined ? (
```

and close the new conditional after the existing section's closing `</div>` (the one that currently ends the Waiver Wire `ld-lineup-section` block, just before the closing `</article>`):

```jsx
                  )}
                </div>
                )}
              </article>
```

- [ ] **Step 3: Lint**

Run: `cd frontend && npm run lint`
Expected: No errors

- [ ] **Step 4: Manual verification**

With at least one ESPN league configured and synced (Tasks 1-4 complete) and at least one Sleeper league:
Run: `npm run dev`
Confirm on the Dashboard:
- The Sleeper league's card still shows its Waiver Wire section as before.
- The ESPN league's card shows no Waiver Wire section at all (not "No trending players available").

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/DashboardPage.jsx
git commit -m "Gate Waiver Wire section to Sleeper leagues only"
```

---

## Task 6: Manual verification against a real ESPN league

This task has no automated tests — it exists to close the spec's two named open risks (combined-view completeness, and whether the two documented non-starting lineup slots are exhaustive) against real data, per the spec's Testing section.

- [ ] **Step 1: Configure real credentials**

Start the app (`docker compose up --build -d` per the project's normal deployment, or the local dev equivalent). Log in, go to Settings, and enter a real `espn_s2`, `swid`, and at least one real ESPN league ID.

- [ ] **Step 2: Trigger a sync and inspect the result**

Click "Sync now" on the Dashboard (or wait for the scheduled sync). Confirm:
- The ESPN league's card appears with the correct team name, score, and opponent.
- Every roster player shows a name, and starters/bench are split correctly.
- No player's position renders as blank or as a raw ESPN string (e.g. `"D/ST"` leaking through unmapped) — if one does, the position/lineup-slot mapping in `espn.py` needs a new entry; add it and re-run Task 1's tests plus this manual check.
- No player's pro team renders as blank when the player is actually rostered by an NFL team — if one does, `PRO_TEAM_MAP` is missing that `proTeamId`; add it.

- [ ] **Step 3: Check for the two named spec risks**

- Compare the combined-view response's team/roster/matchup data against what the ESPN web app itself shows for the same league/week. If anything is missing or looks like live/in-progress scoring is stale, that's the "combined multi-view request may return less than separate calls" risk named in the spec — note it, and if it reproduces, switch `_get_combined_view` to two requests (`mTeam`+`mRoster`+`mSettings` in one, `mMatchupScore` in a second) rather than the current single combined call.
- Check whether the real league has any roster slot beyond Bench (`20`) and IR (`21`) that should also count as non-starting (e.g. a taxi squad, common in dynasty leagues). If so, add its `lineupSlotId` to the non-starting check in `espn.py` and add a corresponding test to `test_espn_adapter.py`.

- [ ] **Step 4: Record findings**

If either risk in Step 3 reproduced and required a code change, commit that fix now with a clear message describing what was found (e.g. "Fix ESPN taxi-squad slot (ID 22) being counted as a starter"). If nothing reproduced, no commit is needed for this task — the manual check itself is the deliverable.

---

## Task 7: Final review

- [ ] Run the full backend suite one more time: `cd backend && python3 -m pytest -q` — expect all tests passing.
- [ ] Run `cd frontend && npm run build` — expect a clean production build with no errors.
- [ ] Confirm `git log` shows one commit per task above (Tasks 1-5, plus Task 6 only if it produced a fix).
- [ ] Use the superpowers:finishing-a-development-branch skill to close out the branch/worktree per its process (test verification, base-branch confirmation, then the merge/PR/keep-as-is menu).
