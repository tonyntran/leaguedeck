# Waiver Wire Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Waiver Wire" section to each league's dashboard card, showing Sleeper's platform-wide trending-add players filtered to exclude anyone already rostered in that specific league.

**Architecture:** A new `sleeper.get_trending_adds()` adapter function calls Sleeper's trending endpoint. A new `GET /leagues/{league_id}/waiver-wire` route cross-references that against the already-synced `Team.roster_json` rows for that league (no new Sleeper call needed for the exclusion set) and returns the filtered, sorted list. Computed live per request, not stored by the sync job. The frontend fetches this per league once `getLeagues()` resolves and renders it as a new section on each team card.

**Tech Stack:** Same as the rest of the project — FastAPI/SQLAlchemy backend, React frontend, no new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-07-waiver-wire-design.md` (reviewed and revised — read it for the full rationale behind each decision below).

## Global Constraints

- No positional-need matching and no drop suggestions — trending + not-already-rostered only, per the spec's scope decision.
- Computed live per request, never stored by `sync_all_platforms`/`SyncLog` — "trending" is a live concept.
- The endpoint must never 500 due to a Sleeper failure — a hiccup on the trending/players-map calls degrades to an empty list, not an error page.
- Query params for `get_trending_adds()` are embedded in the URL via f-string, not passed via httpx's `params=` kwarg — this keeps the existing adapter test mocks (which fake `httpx.get` keyed by exact URL string) working unchanged.
- Unknown player IDs (trending but not in `get_players_map()`) use the exact same fallback `normalize_league` already established: raw ID for name, `None` for position/team.
- A league with zero synced `Team` rows returns an empty list, not the raw unfiltered trending set.
- Non-Sleeper `League.platform` (or a missing `league_id`) returns 404.
- On the frontend, each league's waiver-wire fetch is independent — one league's failure must not affect any other league's display or the rest of the dashboard.

---

## Task 1: Sleeper adapter — trending adds

**Files:**
- Modify: `backend/app/adapters/sleeper.py`
- Modify: `backend/tests/test_sleeper_adapter.py`

**Interfaces:**
- Produces: `get_trending_adds(lookback_hours: int = 24, limit: int = 25) -> list[dict]`, returning `[{"player_id": str, "count": int}, ...]` exactly as Sleeper's API returns it. Task 2's router calls this directly by name (`sleeper.get_trending_adds()`).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_sleeper_adapter.py`:

```python
def test_get_trending_adds_returns_platform_wide_list(monkeypatch):
    def fake_get(url, timeout=10.0):
        assert url == f"{sleeper.SLEEPER_BASE_URL}/players/nfl/trending/add?lookback_hours=24&limit=25"
        return FakeResponse([{"player_id": "p1", "count": 42}, {"player_id": "p2", "count": 10}])

    monkeypatch.setattr(httpx, "get", fake_get)
    result = sleeper.get_trending_adds()
    assert result == [{"player_id": "p1", "count": 42}, {"player_id": "p2", "count": 10}]


def test_get_trending_adds_honors_custom_params(monkeypatch):
    def fake_get(url, timeout=10.0):
        assert url == f"{sleeper.SLEEPER_BASE_URL}/players/nfl/trending/add?lookback_hours=48&limit=10"
        return FakeResponse([])

    monkeypatch.setattr(httpx, "get", fake_get)
    result = sleeper.get_trending_adds(lookback_hours=48, limit=10)
    assert result == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python3 -m pytest tests/test_sleeper_adapter.py -k trending -v`
Expected: FAIL with `AttributeError: module 'app.adapters.sleeper' has no attribute 'get_trending_adds'`

- [ ] **Step 3: Write the implementation**

In `backend/app/adapters/sleeper.py`, add (near `get_players_map`, after `get_user_id`):

```python
def get_trending_adds(lookback_hours: int = 24, limit: int = 25) -> list[dict]:
    """Platform-wide trending adds across all of Sleeper — NOT specific to
    any one league. Returns [{"player_id": str, "count": int}, ...] exactly
    as Sleeper returns it; callers cross-reference against their own
    league's rosters to personalize it."""
    resp = httpx.get(
        f"{SLEEPER_BASE_URL}/players/nfl/trending/add"
        f"?lookback_hours={lookback_hours}&limit={limit}",
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_sleeper_adapter.py -k trending -v`
Expected: PASS

- [ ] **Step 5: Run the full backend suite**

Run: `python3 -m pytest -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add backend/app/adapters/sleeper.py backend/tests/test_sleeper_adapter.py
git commit -m "Add Sleeper trending-adds adapter function"
```

---

## Task 2: Waiver wire API endpoint

**Files:**
- Modify: `backend/app/routers/leagues.py`
- Create: `backend/tests/test_waiver_wire_router.py`

**Interfaces:**
- Consumes: `sleeper.get_trending_adds()`, `sleeper.get_players_map()` (Task 1), `app.models.{League,Team}` (existing), `app.auth.require_auth` (existing).
- Produces: `GET /leagues/{league_id}/waiver-wire` returning `[{"player_id", "name", "position", "team", "trend_count"}]` sorted by `trend_count` descending — Task 3's frontend consumes this exact shape.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_waiver_wire_router.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_waiver_wire_router.py -v`
Expected: FAIL with `404 Not Found` (route doesn't exist yet)

- [ ] **Step 3: Write the implementation**

In `backend/app/routers/leagues.py`:

1. Change the import line `from fastapi import APIRouter, Depends` to `from fastapi import APIRouter, Depends, HTTPException`.
2. Add `from app.adapters import sleeper` to the imports (alongside the existing `from app.models import ...` line).
3. Append this route to the `router` object (the one with `prefix="/leagues"`, not `sync_status_router`):

```python
@router.get("/{league_id}/waiver-wire")
def get_waiver_wire(league_id: int, db: Session = Depends(get_db)):
    league = db.query(League).filter(League.id == league_id).first()
    if league is None or league.platform != "sleeper":
        raise HTTPException(status_code=404, detail="League not found")

    teams = db.query(Team).filter(Team.league_id == league.id).all()
    if not teams:
        return []

    rostered_ids = {
        player["player_id"] for team in teams for player in json.loads(team.roster_json)
    }

    try:
        trending = sleeper.get_trending_adds()
        players_map = sleeper.get_players_map()
    except Exception:
        # A Sleeper hiccup on this nice-to-have feature shouldn't read as a
        # broken dashboard — degrade to no suggestions rather than a 500.
        return []

    available = [
        {
            "player_id": t["player_id"],
            "name": players_map.get(t["player_id"], {}).get("full_name", t["player_id"]),
            "position": players_map.get(t["player_id"], {}).get("position"),
            "team": players_map.get(t["player_id"], {}).get("team"),
            "trend_count": t["count"],
        }
        for t in trending
        if t["player_id"] not in rostered_ids
    ]
    return sorted(available, key=lambda p: p["trend_count"], reverse=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_waiver_wire_router.py -v`
Expected: PASS

- [ ] **Step 5: Run the full backend suite**

Run: `python3 -m pytest -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/leagues.py backend/tests/test_waiver_wire_router.py
git commit -m "Add GET /leagues/{id}/waiver-wire endpoint"
```

---

## Task 3: Frontend — Waiver Wire section

**Files:**
- Modify: `frontend/src/api/client.js`
- Modify: `frontend/src/pages/DashboardPage.jsx`
- Modify: `frontend/src/index.css`

**Interfaces:**
- Consumes: `GET /leagues/{league_id}/waiver-wire` (Task 2).
- Produces: a "Waiver Wire" section rendered on each league's team card.

**Note:** no automated frontend tests exist in this project (consistent with prior tasks) — verify manually via the running app (`docker compose up --build`, or `npm run dev` against a running backend).

- [ ] **Step 1: Add the API client function**

In `frontend/src/api/client.js`, add after `getSyncStatus`:

```js
export function getWaiverWire(leagueId) {
  return request(`/leagues/${leagueId}/waiver-wire`)
}
```

- [ ] **Step 2: Add per-league waiver-wire state and fetching**

In `frontend/src/pages/DashboardPage.jsx`:

1. Add `getWaiverWire` to the import from `'../api/client'`.
2. Add a new piece of state alongside the existing ones:

```js
const [waiverWire, setWaiverWire] = useState({})
```

3. Replace the `loadData` function's `getLeagues()` call with a version that fans out per-league waiver-wire fetches once the league list is known (league IDs don't exist before this resolves, so this must chain after, not run "alongside" it):

```js
function loadData() {
  getLeagues()
    .then((data) => {
      setLeagues(data)
      data.forEach((league) => {
        getWaiverWire(league.id)
          .then((players) => setWaiverWire((prev) => ({ ...prev, [league.id]: players })))
          .catch(() => setWaiverWire((prev) => ({ ...prev, [league.id]: [] })))
      })
    })
    .catch(() => setError('Could not load leagues. Log in and configure Settings first.'))
  getSyncStatus()
    .then(setSyncStatus)
    .catch(() => {})
}
```

- [ ] **Step 3: Render the Waiver Wire section**

In the same file, inside the `<article className="ld-team-card ld-mine">` block, after the existing Bench `<div className="ld-lineup-section ld-bench">...</div>` block, add:

```jsx
<div className="ld-lineup-section">
  <h3 className="ld-lineup-label">Waiver wire</h3>
  {waiverWire[league.id] === undefined ? (
    <p className="ld-status">Loading…</p>
  ) : waiverWire[league.id].length === 0 ? (
    <p className="ld-status">No trending players available right now.</p>
  ) : (
    waiverWire[league.id].map((player) => (
      <div className="ld-waiver-row" key={player.player_id}>
        <span className="ld-pos">{player.position}</span>
        <span className="ld-nm">{player.name}</span>
        <span className="ld-tm">{player.team}</span>
        <span className="ld-trend">{player.trend_count}</span>
      </div>
    ))
  )}
</div>
```

- [ ] **Step 4: Add the row styling**

In `frontend/src/index.css`, add after the existing `.ld-roster-row` rules:

```css
.ld-waiver-row {
  display: flex;
  gap: 0.5rem;
  align-items: baseline;
  font-size: 0.85rem;
  padding: 0.2rem 0;
  color: var(--ld-tc-ink);
  font-family: -apple-system, BlinkMacSystemFont, sans-serif;
}
.ld-waiver-row .ld-pos {
  font-size: 0.68rem;
  font-weight: 700;
  width: 2rem;
  flex-shrink: 0;
  color: var(--ld-tc-pos);
}
.ld-waiver-row .ld-nm {
  overflow: hidden;
  text-overflow: ellipsis;
}
.ld-waiver-row .ld-tm {
  margin-left: auto;
  font-size: 0.72rem;
  flex-shrink: 0;
  color: var(--ld-tc-tm);
}
.ld-waiver-row .ld-trend {
  font-variant-numeric: tabular-nums;
  font-weight: bold;
  font-size: 0.72rem;
  flex-shrink: 0;
  color: var(--ld-tc-score);
  width: 2.4rem;
  text-align: right;
}
```

- [ ] **Step 5: Manually verify**

```bash
docker compose up --build -d
```

Log in, confirm each league's team card now shows a "Waiver wire" section below Bench listing trending players not already on any roster in that league, with trend counts descending. Confirm the rest of the dashboard (Starting Lineup, Bench, Sync now) still works unchanged.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.js frontend/src/pages/DashboardPage.jsx frontend/src/index.css
git commit -m "Add Waiver Wire section to the dashboard"
```
