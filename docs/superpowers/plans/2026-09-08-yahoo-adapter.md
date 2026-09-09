# Yahoo Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Yahoo as LeagueDeck's third platform — OAuth2 (oob flow) credential setup in Settings, a sync job that normalizes Yahoo leagues into the same `League`/`Team` rows Sleeper and ESPN already produce, and the small frontend additions needed to complete the picture (Yahoo section in Settings, Yahoo team-header link on the dashboard).

**Architecture:** A new `app/adapters/yahoo.py` handles both the OAuth2 token lifecycle (authorize URL, code exchange, refresh) and league normalization, producing the exact shape `sleeper.normalize_league`/`espn.normalize_league` already produce. A new `sync_yahoo(db)` in `app/sync.py` mirrors `sync_sleeper`/`sync_espn`'s per-league isolation shape, with an added token-refresh-before-sync step specific to Yahoo's short-lived access tokens. Settings gets four new endpoints under `/settings/yahoo` (GET/PUT for credentials, plus GET authorize-url and POST authorize for the multi-step OAuth handshake) since Yahoo's setup is a real handshake, not a single form submission like ESPN's. Yahoo's raw JSON is a mechanical XML→JSON conversion with two different list-shape artifacts depending on context — this is the single riskiest part of this plan and is called out explicitly in Task 2 and the manual verification task.

**Tech Stack:** Same as the rest of the project — FastAPI/SQLAlchemy backend (`httpx` for both the OAuth and fantasy-API calls, `cryptography.fernet` via the existing `app/crypto.py`), React frontend, no new dependencies (a real option — taking `yfpy` as a dependency — was considered and rejected; see the spec).

**Spec:** `docs/superpowers/specs/2026-09-08-yahoo-adapter-design.md` (reviewed and revised — read it in full before starting; it explains the reasoning behind every decision below, especially the JSON-shape research and the season-rollover limitation).

## Global Constraints

- Per-player object contract, matching Sleeper's/ESPN's exactly: `{"player_id": str, "name": str, "position": str | None, "team": str | None, "is_starter": bool, "actual_points": None, "projected_points": None}` — the last two are always `None` for Yahoo in this pass (non-goal, see spec).
- League-level output contract, matching Sleeper's/ESPN's exactly: `{"platform", "platform_league_id", "name", "season": str, "teams": [...]}` with each team having `platform_team_id, name, is_mine, roster_json, points_for, opponent_name, opponent_points, week`.
- League key is `f"nfl.l.{league_id}"` — no separate game-ID-resolution call (the literal string `"nfl"` resolves to the current season server-side).
- Every value in Yahoo's JSON arrives as a string, including numbers and boolean-flavored flags. Cast `float()` for points, `int()` for week; never use a bare truthiness check on a string flag (`"0"` is truthy in Python) — always compare with `== "1"` explicitly. `season` stays a string (no cast needed, but must come from wherever the league metadata response actually places it).
- `is_mine` is determined by matching the stored `yahoo_guid` (from the OAuth token response's `xoauth_yahoo_guid`) against each team's manager GUIDs — never a team-level ownership flag.
- OAuth token endpoint calls (`exchange_code_for_tokens`, `refresh_access_token`) send `client_id`/`client_secret` via an HTTP Basic Auth header (base64 of `client_id:client_secret`), never as body parameters. Both calls include `redirect_uri=oob` in the body — easy to forget on the refresh call specifically.
- Storage split: `yahoo_client_id`, `yahoo_league_ids`, `yahoo_guid`, `yahoo_token_expires_at` go in `AppSetting` (not secret, may be echoed); `yahoo_client_secret`, `yahoo_access_token`, `yahoo_refresh_token` go in the encrypted `Secret` table. `GET /settings/yahoo` never echoes `client_secret` or the tokens.
- `PUT /settings/yahoo`: `client_id` and `league_ids` always overwrite; `client_secret` is optional (omitted or blank means "leave unchanged," matching ESPN's `espn_s2`/`swid` contract) and never touches the stored tokens.
- `sync_yahoo(db)` must write **zero** `SyncLog` rows when unconfigured (no league IDs, no client credentials, or not yet authorized) — matching the fix already applied to `sync_sleeper`/`sync_espn`.
- Per-league isolation in `sync_yahoo`, mirroring `sync_sleeper`/`sync_espn`: one bad Yahoo league must not block any other Yahoo league or either other platform.
- Never log a pasted authorization code, a token response body, or the Basic Auth header value.
- No lineup writes; no per-player actual/projected points for Yahoo in this pass (see spec's Scope decisions).

---

## Task 1: Yahoo adapter — OAuth token functions and the JSON-unwrapping helpers

**Files:**
- Create: `backend/app/adapters/yahoo.py`
- Create: `backend/tests/test_yahoo_adapter.py`

**Interfaces:**
- Produces: `yahoo.get_authorize_url(client_id: str) -> str`; `yahoo.exchange_code_for_tokens(client_id: str, client_secret: str, code: str) -> dict` returning `{"access_token": str, "refresh_token": str, "expires_in": int, "yahoo_guid": str | None}`; `yahoo.refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict` returning the same shape; `yahoo._reformat(obj) -> dict`, `yahoo._unwrap_list(obj) -> list` (the two JSON-shape helpers Task 2's `normalize_league` will use). Also produces `yahoo.YahooAdapterError` (defined here, raised nowhere yet — Task 2 raises it for a league the user isn't a member of).
- Consumes: nothing from other tasks (uses only `httpx`, `base64`, stdlib).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_yahoo_adapter.py`:

```python
import base64

import httpx

from app.adapters import yahoo


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def test_unwrap_list_converts_indexed_dict_to_list():
    assert yahoo._unwrap_list({"0": "a", "1": "b", "count": 2}) == ["a", "b"]


def test_unwrap_list_passes_through_a_real_list_unchanged():
    assert yahoo._unwrap_list(["a", "b"]) == ["a", "b"]


def test_unwrap_list_unwraps_a_single_wrapped_value():
    """XML has no distinct 'definitely one item' representation, so a lone
    value can arrive wrapped as {"0": value} with no "count" sibling."""
    assert yahoo._unwrap_list({"0": {"key": "value"}}) == [{"key": "value"}]


def test_unwrap_list_returns_empty_list_for_anything_else():
    assert yahoo._unwrap_list(None) == []
    assert yahoo._unwrap_list("not a collection") == []


def test_reformat_merges_a_list_of_single_key_dicts():
    """Mixed sibling elements at the same XML level (e.g. <league> next to
    <time> next to <copyright>) render as a genuine JSON array of
    single-key dicts, not an object -- this must merge into one dict."""
    assert yahoo._reformat([{"league": {"name": "Test"}}, {"time": "1.2"}]) == {
        "league": {"name": "Test"},
        "time": "1.2",
    }


def test_reformat_passes_through_a_dict_unchanged():
    assert yahoo._reformat({"league": {"name": "Test"}}) == {"league": {"name": "Test"}}


def test_reformat_returns_empty_dict_for_anything_else():
    assert yahoo._reformat(None) == {}
    assert yahoo._reformat("not a mapping") == {}


def test_navigate_walks_nested_keys_through_mixed_shapes():
    fantasy_content = [
        {"league": [{"name": "Test League"}, {"season": "2026"}]},
        {"time": "0.1"},
    ]
    assert yahoo._navigate(fantasy_content, "league") == [
        {"name": "Test League"},
        {"season": "2026"},
    ]


def test_get_authorize_url_builds_oob_request():
    url = yahoo.get_authorize_url("my-client-id")
    assert url == (
        "https://api.login.yahoo.com/oauth2/request_auth"
        "?client_id=my-client-id&redirect_uri=oob&response_type=code"
    )


def test_exchange_code_for_tokens_sends_basic_auth_and_parses_response(monkeypatch):
    def fake_post(url, headers=None, data=None, timeout=10.0):
        assert url == "https://api.login.yahoo.com/oauth2/get_token"
        expected_token = base64.b64encode(b"client123:secret456").decode()
        assert headers == {"Authorization": f"Basic {expected_token}"}
        assert data == {
            "redirect_uri": "oob",
            "code": "the-code",
            "grant_type": "authorization_code",
        }
        return FakeResponse(
            {
                "access_token": "at-1",
                "refresh_token": "rt-1",
                "expires_in": 3600,
                "xoauth_yahoo_guid": "GUID123",
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    result = yahoo.exchange_code_for_tokens("client123", "secret456", "the-code")
    assert result == {
        "access_token": "at-1",
        "refresh_token": "rt-1",
        "expires_in": 3600,
        "yahoo_guid": "GUID123",
    }


def test_refresh_access_token_sends_refresh_grant_and_redirect_uri(monkeypatch):
    """redirect_uri=oob is required on the refresh grant too -- easy to
    omit by mistake, and omitting it risks an error indistinguishable from
    an expired refresh token."""
    def fake_post(url, headers=None, data=None, timeout=10.0):
        assert data == {
            "redirect_uri": "oob",
            "refresh_token": "old-refresh",
            "grant_type": "refresh_token",
        }
        return FakeResponse(
            {"access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600}
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    result = yahoo.refresh_access_token("client123", "secret456", "old-refresh")
    assert result["access_token"] == "at-2"
    assert result["refresh_token"] == "rt-2"
    assert result["yahoo_guid"] is None  # not present on this response


def test_exchange_code_for_tokens_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: FakeResponse({"error": "invalid_grant"}, status_code=400)
    )

    raised = False
    try:
        yahoo.exchange_code_for_tokens("client123", "secret456", "bad-code")
    except httpx.HTTPStatusError:
        raised = True
    assert raised
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python3 -m pytest tests/test_yahoo_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.adapters.yahoo'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/adapters/yahoo.py`:

```python
import base64
import logging

import httpx

logger = logging.getLogger(__name__)

YAHOO_OAUTH_HOST = "https://api.login.yahoo.com/oauth2"
YAHOO_FANTASY_BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2"
OOB_REDIRECT_URI = "oob"


class YahooAdapterError(Exception):
    pass


def _reformat(obj) -> dict:
    """Yahoo's ?format=json output is a mechanical XML->JSON conversion
    with two different list-like shapes depending on context: mixed
    sibling elements at the same XML level (e.g. <league> next to <time>
    next to <copyright>) render as a genuine JSON array of single-key
    dicts, while repeated identical sibling elements (e.g. several <team>
    elements) render as an indexed object {"0": item0, "1": item1, ...,
    "count": N}. This merges either shape into one flat dict so a single
    .get(key) works regardless of which shape a particular response used
    at a particular nesting level. Verified against the actual navigation
    logic (query()/reformat_json_list) in yfpy, the leading Python wrapper
    for this API -- not yet confirmed against a live response from this
    app. See the design spec's Testing section: this is the first thing to
    verify once real Yahoo API access is available."""
    if isinstance(obj, list):
        merged: dict = {}
        for item in obj:
            if isinstance(item, dict):
                merged.update(item)
        return merged
    if isinstance(obj, dict):
        return obj
    return {}


def _unwrap_list(obj) -> list:
    """Given a value expected to be a repeated collection (a league's
    teams, a team's roster entries, a matchup's two teams), returns a
    plain Python list -- dropping the "count" sentinel from Yahoo's
    indexed-object shape, or passing a genuine JSON list through
    unchanged. A single wrapped value (no "count" sibling, since XML has
    no distinct "definitely one item" representation) also unwraps
    correctly; callers take element [0] at the call site if they know
    only one value is expected."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        return [v for k, v in obj.items() if k != "count"]
    return []


def _navigate(fantasy_content, *keys: str):
    """Walks fantasy_content by each key in turn, reformatting at each
    step so mixed-shape nesting doesn't need a special case per level.
    Mirrors yfpy's query() drilling logic, confirmed against its source."""
    current = fantasy_content
    for key in keys:
        current = _reformat(current).get(key)
    return current


def _basic_auth_header(client_id: str, client_secret: str) -> dict:
    token = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def get_authorize_url(client_id: str) -> str:
    """Builds the URL the user opens in a new tab to approve access. Yahoo
    shows a one-time verification code on-screen (the "oob" -- out of
    band -- flow) rather than redirecting anywhere, since this app has no
    HTTPS listener to receive a redirect."""
    return (
        f"{YAHOO_OAUTH_HOST}/request_auth"
        f"?client_id={client_id}&redirect_uri={OOB_REDIRECT_URI}&response_type=code"
    )


def _parse_token_response(resp: httpx.Response) -> dict:
    resp.raise_for_status()
    data = resp.json()
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_in": data["expires_in"],
        "yahoo_guid": data.get("xoauth_yahoo_guid"),
    }


def exchange_code_for_tokens(client_id: str, client_secret: str, code: str) -> dict:
    """Exchanges a one-time verification code (pasted by the user from
    Yahoo's oob approval page) for an access/refresh token pair. Never log
    the code, this function's return value, or the raw response body --
    all are live credentials."""
    resp = httpx.post(
        f"{YAHOO_OAUTH_HOST}/get_token",
        headers=_basic_auth_header(client_id, client_secret),
        data={
            "redirect_uri": OOB_REDIRECT_URI,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=10.0,
    )
    return _parse_token_response(resp)


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """Same endpoint as exchange_code_for_tokens, but trading a refresh
    token for a new access token instead of a one-time code. redirect_uri
    is required on this call too, even though no redirect happens -- omit
    it and the resulting error looks identical to an expired refresh
    token."""
    resp = httpx.post(
        f"{YAHOO_OAUTH_HOST}/get_token",
        headers=_basic_auth_header(client_id, client_secret),
        data={
            "redirect_uri": OOB_REDIRECT_URI,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=10.0,
    )
    return _parse_token_response(resp)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_yahoo_adapter.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Run the full backend suite**

Run: `python3 -m pytest -q`
Expected: All tests pass (nothing else references `app.adapters.yahoo` yet)

- [ ] **Step 6: Commit**

```bash
git add backend/app/adapters/yahoo.py backend/tests/test_yahoo_adapter.py
git commit -m "Add Yahoo adapter: OAuth token functions and JSON-shape helpers"
```

---

## Task 2: Yahoo adapter — `normalize_league`

**Files:**
- Modify: `backend/app/adapters/yahoo.py`
- Modify: `backend/tests/test_yahoo_adapter.py`

**Interfaces:**
- Consumes: `_reformat`, `_unwrap_list`, `_navigate`, `YahooAdapterError` (Task 1, same file).
- Produces: `yahoo.normalize_league(league_id: str, access_token: str, my_guid: str) -> dict`, returning the shared League/Team shape described in Global Constraints. Task 4's `sync.py` calls this directly by name.

**Important context for whoever implements this task:** this is the riskiest task in the whole plan. The exact shape of a real Yahoo API response has not been confirmed live (see the spec's Testing section) — the fixtures below represent the best-available understanding from Yahoo's own reference docs and yfpy's confirmed parsing logic, not a captured real response. If a real response (once Yahoo API access is granted — see the spec's blocking prerequisite) turns out to nest differently than these fixtures assume, that is expected, not a sign this task was done wrong — adjust the parsing to match reality and update the fixtures to match. Do not treat a mismatch as a task failure; treat it as the verification this spec always said would be needed.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_yahoo_adapter.py`:

```python
def _fantasy_content(league_body):
    """Wraps a league response body the way Yahoo's top-level envelope
    does: fantasy_content as a list of single-key dicts (mixed sibling
    elements), one of which is "league"."""
    return {"fantasy_content": [{"league": league_body}]}


def _league_metadata_response():
    return _fantasy_content(
        [
            {"name": "Test League", "season": "2026", "current_week": "3"},
        ]
    )


def _league_teams_response():
    return _fantasy_content(
        [
            {"name": "Test League"},
            {
                "teams": {
                    "0": {
                        "team": [
                            {"team_key": "nfl.l.999.t.1", "name": "Team One"},
                            {"managers": {"0": {"manager": {"guid": "MY-GUID"}}}},
                        ]
                    },
                    "1": {
                        "team": [
                            {"team_key": "nfl.l.999.t.2", "name": "Team Two"},
                            {"managers": {"0": {"manager": {"guid": "OTHER-GUID"}}}},
                        ]
                    },
                    "count": 2,
                }
            },
        ]
    )


def _team_roster_response(team_name):
    return _fantasy_content(
        [
            {"name": "unused"},
        ]
    ) | {
        "fantasy_content": [
            {
                "team": [
                    {"name": team_name},
                    {
                        "roster": {
                            "0": {"players": {"count": 0}},
                        }
                    },
                ]
            }
        ]
    }


def _roster_with_players(entries):
    return {
        "fantasy_content": [
            {
                "team": [
                    {"name": "Team One"},
                    {
                        "roster": {
                            "0": {
                                "players": {
                                    **{str(i): {"player": e} for i, e in enumerate(entries)},
                                    "count": len(entries),
                                }
                            }
                        }
                    },
                ]
            }
        ]
    }


def _player_entry(player_id, name, position, pro_team, slot):
    return [
        {
            "player_id": player_id,
            "name": {"full": name},
            "display_position": position,
            "editorial_team_abbr": pro_team,
        },
        {"selected_position": [{"position": slot}]},
    ]


def _scoreboard_response(week, team_key_a, points_a, team_key_b, points_b):
    return {
        "fantasy_content": [
            {
                "league": [
                    {"name": "unused"},
                    {
                        "scoreboard": {
                            "0": {
                                "matchups": {
                                    "0": {
                                        "matchup": {
                                            "week": week,
                                            "teams": {
                                                "0": {
                                                    "team": [
                                                        {"team_key": team_key_a},
                                                        {"team_points": {"total": points_a}},
                                                    ]
                                                },
                                                "1": {
                                                    "team": [
                                                        {"team_key": team_key_b},
                                                        {"team_points": {"total": points_b}},
                                                    ]
                                                },
                                                "count": 2,
                                            },
                                        }
                                    },
                                    "count": 1,
                                }
                            }
                        }
                    },
                ]
            }
        ]
    }


def test_normalize_league_builds_teams_with_roster_and_score(monkeypatch):
    league_id = "999"
    my_guid = "MY-GUID"

    responses = {
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/metadata": _league_metadata_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/teams": _league_teams_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/scoreboard": _scoreboard_response(
            "3", "nfl.l.999.t.1", "100.5", "nfl.l.999.t.2", "90.2"
        ),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.1/roster;week=3": _roster_with_players(
            [_player_entry("111", "Player One", "RB", "KC", "QB")]
        ),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.2/roster;week=3": _roster_with_players([]),
    }

    def fake_get(url, headers=None, timeout=15.0):
        assert headers == {"Authorization": "Bearer test-access-token"}
        return FakeResponse(responses[url])

    monkeypatch.setattr(httpx, "get", fake_get)

    result = yahoo.normalize_league(league_id, "test-access-token", my_guid)

    assert result["platform"] == "yahoo"
    assert result["platform_league_id"] == "999"
    assert result["name"] == "Test League"
    assert result["season"] == "2026"

    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["platform_team_id"] == "nfl.l.999.t.1"
    assert my_team["name"] == "Team One"
    assert my_team["points_for"] == 100.5
    assert my_team["opponent_name"] == "Team Two"
    assert my_team["opponent_points"] == 90.2
    assert my_team["week"] == 3

    player = my_team["roster_json"][0]
    assert player == {
        "player_id": "111",
        "name": "Player One",
        "position": "RB",
        "team": "KC",
        "is_starter": True,
        "actual_points": None,
        "projected_points": None,
    }

    other_team = next(t for t in result["teams"] if not t["is_mine"])
    assert other_team["name"] == "Team Two"


def test_normalize_league_marks_bench_slot_as_not_starting(monkeypatch):
    league_id = "999"
    responses = {
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/metadata": _league_metadata_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/teams": _league_teams_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/scoreboard": _scoreboard_response(
            "3", "nfl.l.999.t.1", "0", "nfl.l.999.t.2", "0"
        ),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.1/roster;week=3": _roster_with_players(
            [_player_entry("222", "Bench Guy", "WR", "SF", "BN")]
        ),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.2/roster;week=3": _roster_with_players([]),
    }
    monkeypatch.setattr(httpx, "get", lambda url, headers=None, timeout=15.0: FakeResponse(responses[url]))

    result = yahoo.normalize_league(league_id, "test-access-token", "MY-GUID")
    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["roster_json"][0]["is_starter"] is False


def test_normalize_league_casts_string_points_and_week_to_correct_types(monkeypatch):
    """Every value in Yahoo's JSON arrives as a string -- this must not
    leak a str into Team.points_for (Float) or Team.week (Integer)."""
    league_id = "999"
    responses = {
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/metadata": _league_metadata_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/teams": _league_teams_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/scoreboard": _scoreboard_response(
            "3", "nfl.l.999.t.1", "123.45", "nfl.l.999.t.2", "67.89"
        ),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.1/roster;week=3": _roster_with_players([]),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.2/roster;week=3": _roster_with_players([]),
    }
    monkeypatch.setattr(httpx, "get", lambda url, headers=None, timeout=15.0: FakeResponse(responses[url]))

    result = yahoo.normalize_league(league_id, "test-access-token", "MY-GUID")
    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["points_for"] == 123.45
    assert isinstance(my_team["points_for"], float)
    assert my_team["week"] == 3
    assert isinstance(my_team["week"], int)


def test_normalize_league_matches_guid_for_is_mine_not_a_team_flag(monkeypatch):
    """is_mine must come from comparing the authenticated user's own GUID
    against each team's manager GUIDs -- never a team-level flag."""
    league_id = "999"
    responses = {
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/metadata": _league_metadata_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/teams": _league_teams_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/scoreboard": _scoreboard_response(
            "3", "nfl.l.999.t.1", "0", "nfl.l.999.t.2", "0"
        ),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.1/roster;week=3": _roster_with_players([]),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.2/roster;week=3": _roster_with_players([]),
    }
    monkeypatch.setattr(httpx, "get", lambda url, headers=None, timeout=15.0: FakeResponse(responses[url]))

    # Authenticate as the SECOND team's manager this time.
    result = yahoo.normalize_league(league_id, "test-access-token", "OTHER-GUID")
    assert [t["platform_team_id"] for t in result["teams"] if t["is_mine"]] == ["nfl.l.999.t.2"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_yahoo_adapter.py -k normalize_league -v`
Expected: FAIL with `AttributeError: module 'app.adapters.yahoo' has no attribute 'normalize_league'`

- [ ] **Step 3: Write the implementation**

Append to `backend/app/adapters/yahoo.py`:

```python
BENCH_SLOT = "BN"
IR_SLOTS = ("IR", "IR+", "IR-R")


def _get(url: str, access_token: str) -> dict:
    resp = httpx.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()


def _league_key(league_id: str) -> str:
    return f"nfl.l.{league_id}"


def _get_league_metadata(league_key: str, access_token: str) -> dict:
    data = _get(f"{YAHOO_FANTASY_BASE_URL}/league/{league_key}/metadata", access_token)
    league_data = _navigate(data.get("fantasy_content"), "league")
    return _reformat(league_data)


def _manager_guids(team_entries: list) -> list[str]:
    managers_raw = _reformat(team_entries).get("managers")
    guids = []
    for entry in _unwrap_list(managers_raw):
        manager = _reformat(entry).get("manager") or {}
        guid = manager.get("guid")
        if guid:
            guids.append(guid)
    return guids


def _get_league_teams(league_key: str, access_token: str) -> list[dict]:
    """Returns one dict per team: {"team_key", "name", "manager_guids"}."""
    data = _get(f"{YAHOO_FANTASY_BASE_URL}/league/{league_key}/teams", access_token)
    league_data = _navigate(data.get("fantasy_content"), "league")
    teams_raw = _reformat(league_data).get("teams")
    teams = []
    for entry in _unwrap_list(teams_raw):
        team_entries = _reformat(entry).get("team")
        flat = _reformat(team_entries)
        teams.append(
            {
                "team_key": flat.get("team_key"),
                "name": flat.get("name"),
                "manager_guids": _manager_guids(team_entries),
            }
        )
    return teams


def _get_team_roster(team_key: str, week: int, access_token: str) -> list[dict]:
    data = _get(f"{YAHOO_FANTASY_BASE_URL}/team/{team_key}/roster;week={week}", access_token)
    team_data = _navigate(data.get("fantasy_content"), "team")
    roster_raw = _reformat(team_data).get("roster")
    players_raw = None
    for entry in _unwrap_list(roster_raw):
        candidate = _reformat(entry).get("players")
        if candidate is not None:
            players_raw = candidate
            break

    roster_players = []
    for entry in _unwrap_list(players_raw):
        player_entries = _reformat(entry).get("player")
        flat = _reformat(player_entries)
        name_field = flat.get("name") or {}
        slot_entries = flat.get("selected_position")
        slot = _reformat(_unwrap_list(slot_entries)[0] if _unwrap_list(slot_entries) else {}).get(
            "position"
        )
        player_id = str(flat.get("player_id"))
        roster_players.append(
            {
                "player_id": player_id,
                "name": name_field.get("full") or player_id,
                "position": flat.get("display_position"),
                "team": flat.get("editorial_team_abbr"),
                "is_starter": slot != BENCH_SLOT and slot not in IR_SLOTS,
                "actual_points": None,
                "projected_points": None,
            }
        )
    return roster_players


def _get_scoreboard(league_key: str, access_token: str) -> tuple[int, list[dict]]:
    """Returns (week, matchups), where each matchup is
    [{"team_key", "points"}, {"team_key", "points"}]."""
    data = _get(f"{YAHOO_FANTASY_BASE_URL}/league/{league_key}/scoreboard", access_token)
    league_data = _navigate(data.get("fantasy_content"), "league")
    scoreboard_raw = _reformat(league_data).get("scoreboard")
    matchups_raw = None
    week = None
    for entry in _unwrap_list(scoreboard_raw):
        candidate = _reformat(entry).get("matchups")
        if candidate is not None:
            matchups_raw = candidate
            break

    matchups = []
    for m_entry in _unwrap_list(matchups_raw):
        matchup = _reformat(_reformat(m_entry).get("matchup"))
        if week is None and matchup.get("week") is not None:
            week = int(matchup["week"])
        teams_raw = matchup.get("teams")
        pair = []
        for t_entry in _unwrap_list(teams_raw):
            team_entries = _reformat(t_entry).get("team")
            flat = _reformat(team_entries)
            points = _reformat(flat.get("team_points")).get("total")
            pair.append({"team_key": flat.get("team_key"), "points": float(points or 0.0)})
        if len(pair) == 2:
            matchups.append(pair)
    return week, matchups


def normalize_league(league_id: str, access_token: str, my_guid: str) -> dict:
    """Fetch everything for one Yahoo league and normalize into
    LeagueDeck's shared League/Team shape (the same shape sleeper.
    normalize_league/espn.normalize_league produce)."""
    league_key = _league_key(league_id)
    metadata = _get_league_metadata(league_key, access_token)
    teams = _get_league_teams(league_key, access_token)
    week, matchups = _get_scoreboard(league_key, access_token)

    points_by_team_key: dict[str, float] = {}
    opponent_by_team_key: dict[str, tuple[str | None, float | None]] = {}
    for pair in matchups:
        (a, b) = pair
        points_by_team_key[a["team_key"]] = a["points"]
        points_by_team_key[b["team_key"]] = b["points"]
        opponent_by_team_key[a["team_key"]] = (b["team_key"], b["points"])
        opponent_by_team_key[b["team_key"]] = (a["team_key"], a["points"])

    teams_by_key = {t["team_key"]: t for t in teams}

    result_teams = []
    for team in teams:
        team_key = team["team_key"]
        opponent_key, opponent_points = opponent_by_team_key.get(team_key, (None, None))
        opponent_team = teams_by_key.get(opponent_key)
        roster_players = _get_team_roster(team_key, week, access_token)
        result_teams.append(
            {
                "platform_team_id": team_key,
                "name": team["name"],
                "is_mine": my_guid in team["manager_guids"],
                "roster_json": roster_players,
                "points_for": points_by_team_key.get(team_key, 0.0),
                "opponent_name": opponent_team["name"] if opponent_team else None,
                "opponent_points": opponent_points,
                "week": week,
            }
        )

    return {
        "platform": "yahoo",
        "platform_league_id": league_id,
        "name": metadata.get("name") or f"Yahoo League {league_id}",
        "season": metadata.get("season") or "",
        "teams": result_teams,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_yahoo_adapter.py -v`
Expected: PASS (16 tests total)

- [ ] **Step 5: Run the full backend suite**

Run: `python3 -m pytest -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add backend/app/adapters/yahoo.py backend/tests/test_yahoo_adapter.py
git commit -m "Add Yahoo adapter: normalize_league"
```

---

## Task 3: Settings — `GET/PUT /settings/yahoo`, `GET .../authorize-url`, `POST .../authorize`

**Files:**
- Modify: `backend/app/routers/settings.py`
- Modify: `backend/tests/test_settings_router.py`

**Interfaces:**
- Consumes: `yahoo.get_authorize_url`, `yahoo.exchange_code_for_tokens` (Task 1), the existing `_set_setting`/`_get_setting`/`_set_secret`/`_secret_configured` helpers already in `settings.py`.
- Produces: the four endpoints described below. Task 4's `sync_yahoo` reads the same `AppSetting` keys (`yahoo_client_id`, `yahoo_league_ids`, `yahoo_guid`, `yahoo_token_expires_at`) and `Secret` keys (`yahoo_client_secret`, `yahoo_access_token`, `yahoo_refresh_token`) this task writes — those seven exact key name strings matter and must be used verbatim as they appear below.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_settings_router.py`:

```python
def test_yahoo_settings_requires_auth(client):
    resp = client.get("/settings/yahoo")
    assert resp.status_code == 401


def test_get_yahoo_settings_before_any_put_reports_unconfigured(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})

    resp = client.get("/settings/yahoo")
    assert resp.status_code == 200
    assert resp.json() == {
        "client_id": "",
        "league_ids": [],
        "client_secret_configured": False,
        "authorized": False,
    }


def test_put_and_get_yahoo_settings_roundtrip(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})

    put_resp = client.put(
        "/settings/yahoo",
        json={"client_id": "my-client-id", "client_secret": "shh", "league_ids": ["111", "222"]},
    )
    assert put_resp.status_code == 200

    get_resp = client.get("/settings/yahoo")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["client_id"] == "my-client-id"  # not secret, echoed back
    assert data["league_ids"] == ["111", "222"]
    assert data["client_secret_configured"] is True
    assert data["authorized"] is False  # PUT alone never touches tokens
    # The secret itself must never appear anywhere in the response body.
    assert "shh" not in get_resp.text


def test_put_yahoo_settings_with_league_ids_only_preserves_existing_secret(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})
    client.put(
        "/settings/yahoo",
        json={"client_id": "my-client-id", "client_secret": "shh", "league_ids": ["111"]},
    )

    update_resp = client.put(
        "/settings/yahoo", json={"client_id": "my-client-id", "league_ids": ["111", "222"]}
    )
    assert update_resp.status_code == 200

    data = client.get("/settings/yahoo").json()
    assert data["league_ids"] == ["111", "222"]
    assert data["client_secret_configured"] is True


def test_yahoo_authorize_url_requires_client_id(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})

    resp = client.get("/settings/yahoo/authorize-url")
    assert resp.status_code == 400


def test_yahoo_authorize_url_builds_from_stored_client_id(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})
    client.put(
        "/settings/yahoo",
        json={"client_id": "my-client-id", "client_secret": "shh", "league_ids": []},
    )

    resp = client.get("/settings/yahoo/authorize-url")
    assert resp.status_code == 200
    assert resp.json() == {
        "url": (
            "https://api.login.yahoo.com/oauth2/request_auth"
            "?client_id=my-client-id&redirect_uri=oob&response_type=code"
        )
    }


def test_yahoo_authorize_exchanges_code_and_stores_tokens(client, monkeypatch):
    from app.routers import settings as settings_router

    client.post("/auth/login", json={"password": TEST_PASSWORD})
    client.put(
        "/settings/yahoo",
        json={"client_id": "my-client-id", "client_secret": "shh", "league_ids": []},
    )

    monkeypatch.setattr(
        settings_router.yahoo,
        "exchange_code_for_tokens",
        lambda client_id, client_secret, code: {
            "access_token": "at-1",
            "refresh_token": "rt-1",
            "expires_in": 3600,
            "yahoo_guid": "GUID123",
        },
    )

    resp = client.post("/settings/yahoo/authorize", json={"code": "the-code"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    data = client.get("/settings/yahoo").json()
    assert data["authorized"] is True


def test_yahoo_authorize_returns_400_on_failed_exchange(client, monkeypatch):
    from app.routers import settings as settings_router

    client.post("/auth/login", json={"password": TEST_PASSWORD})
    client.put(
        "/settings/yahoo",
        json={"client_id": "my-client-id", "client_secret": "shh", "league_ids": []},
    )

    def raise_error(client_id, client_secret, code):
        raise Exception("invalid_grant")

    monkeypatch.setattr(settings_router.yahoo, "exchange_code_for_tokens", raise_error)

    resp = client.post("/settings/yahoo/authorize", json={"code": "bad-code"})
    assert resp.status_code == 400
    # The exchange failure reason must not leak the raw code or a token.
    assert "bad-code" not in resp.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_settings_router.py -k yahoo -v`
Expected: FAIL with 404s (no `/settings/yahoo` routes yet)

- [ ] **Step 3: Write the implementation**

In `backend/app/routers/settings.py`, update the imports:

```python
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.adapters import yahoo
from app.auth import require_auth
from app.crypto import decrypt_value, encrypt_value
from app.db import get_db
from app.models import AppSetting, Secret
```

Add after `EspnSettings`:

```python
class YahooSettings(BaseModel):
    client_id: str
    client_secret: str | None = None
    league_ids: list[str]


class YahooAuthorizeCode(BaseModel):
    code: str
```

Add after `set_espn_settings`:

```python
@router.get("/yahoo")
def get_yahoo_settings(db: Session = Depends(get_db)):
    league_ids_raw = _get_setting(db, "yahoo_league_ids") or ""
    return {
        "client_id": _get_setting(db, "yahoo_client_id") or "",
        "league_ids": [x.strip() for x in league_ids_raw.split(",") if x.strip()],
        "client_secret_configured": _secret_configured(db, "yahoo_client_secret"),
        "authorized": _secret_configured(db, "yahoo_access_token"),
    }


@router.put("/yahoo")
def set_yahoo_settings(payload: YahooSettings, db: Session = Depends(get_db)):
    # client_id is not secret -- it's an identifier, the same category as
    # Sleeper's username -- so it always overwrites and is echoed by GET.
    _set_setting(db, "yahoo_client_id", payload.client_id.strip())
    if payload.client_secret and payload.client_secret.strip():
        _set_secret(db, "yahoo_client_secret", payload.client_secret.strip())
    _set_setting(
        db,
        "yahoo_league_ids",
        ",".join(x.strip() for x in payload.league_ids if x.strip()),
    )
    return {"ok": True}


@router.get("/yahoo/authorize-url")
def get_yahoo_authorize_url(db: Session = Depends(get_db)):
    client_id = _get_setting(db, "yahoo_client_id")
    if not client_id:
        raise HTTPException(status_code=400, detail="Save a Yahoo client ID first")
    return {"url": yahoo.get_authorize_url(client_id)}


@router.post("/yahoo/authorize")
def authorize_yahoo(payload: YahooAuthorizeCode, db: Session = Depends(get_db)):
    client_id = _get_setting(db, "yahoo_client_id")
    client_secret_encrypted = _get_secret(db, "yahoo_client_secret")
    if not client_id or not client_secret_encrypted:
        raise HTTPException(status_code=400, detail="Save Yahoo credentials first")
    client_secret = decrypt_value(client_secret_encrypted)

    try:
        tokens = yahoo.exchange_code_for_tokens(client_id, client_secret, payload.code)
    except Exception:
        # Never echo Yahoo's raw error body or the pasted code back to the
        # client -- a generic message is enough to act on.
        raise HTTPException(status_code=400, detail="Could not verify that code with Yahoo")

    _set_secret(db, "yahoo_access_token", tokens["access_token"])
    _set_secret(db, "yahoo_refresh_token", tokens["refresh_token"])
    if tokens.get("yahoo_guid"):
        _set_setting(db, "yahoo_guid", tokens["yahoo_guid"])
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=tokens["expires_in"])
    _set_setting(db, "yahoo_token_expires_at", expires_at.isoformat())
    return {"ok": True}
```

Add a `_get_secret` helper next to the existing `_set_secret`/`_secret_configured` (settings.py does not currently have one — `sync.py` has its own, but this router needs its own too, matching the existing pattern of each module owning its own thin DB helpers):

```python
def _get_secret(db: Session, key: str) -> str | None:
    row = db.query(Secret).filter(Secret.key == key).first()
    return row.encrypted_value if row else None
```

(Place this next to `_secret_configured`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_settings_router.py -v`
Expected: PASS (all Sleeper/ESPN tests still pass, all new Yahoo tests pass)

- [ ] **Step 5: Run the full backend suite**

Run: `python3 -m pytest -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add backend/app/routers/settings.py backend/tests/test_settings_router.py
git commit -m "Add Yahoo settings endpoints: credentials and OAuth handshake"
```

---

## Task 4: Sync — `sync_yahoo` with token refresh, wired into `sync_all_platforms`

**Files:**
- Modify: `backend/app/sync.py`
- Create: `backend/tests/test_yahoo_sync.py`

**Interfaces:**
- Consumes: `yahoo.normalize_league`, `yahoo.refresh_access_token` (Task 1/2), `app.crypto.{decrypt_value}` (existing), the `AppSetting` keys `yahoo_client_id`/`yahoo_league_ids`/`yahoo_guid`/`yahoo_token_expires_at` and `Secret` keys `yahoo_client_secret`/`yahoo_access_token`/`yahoo_refresh_token` (Task 3 writes these).
- Produces: `sync_yahoo(db: Session) -> None`, called from `sync_all_platforms()` alongside `sync_sleeper(db)` and `sync_espn(db)`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_yahoo_sync.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_yahoo_sync.py -v`
Expected: FAIL with `ImportError: cannot import name 'sync_yahoo'`

- [ ] **Step 3: Write the implementation**

In `backend/app/sync.py`, update imports (adding the Yahoo adapter import and
extending the existing crypto import with `encrypt_value`, needed by the new
`_set_secret` helper below):

```python
from app.adapters import espn as espn_adapter
from app.adapters import sleeper as sleeper_adapter
from app.adapters import yahoo as yahoo_adapter
from app.crypto import decrypt_value, encrypt_value
```

Add after `sync_espn` (before `sync_all_platforms`):

```python
YAHOO_TOKEN_REFRESH_MARGIN_SECONDS = 5 * 60


def _sync_one_yahoo_league(db: Session, league_id: str, access_token: str, my_guid: str) -> None:
    """Refresh a single Yahoo league. Caller commits/rolls back so that one
    league's failure cannot discard another league's work."""
    normalized = yahoo_adapter.normalize_league(league_id, access_token, my_guid)

    league = (
        db.query(League)
        .filter(League.platform == "yahoo", League.platform_league_id == league_id)
        .first()
    )
    if league is None:
        league = League(
            platform="yahoo",
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


def sync_yahoo(db: Session) -> None:
    """Yahoo setup is genuinely optional and requires a full OAuth handshake
    beyond just saving credentials -- checks configuration (client
    credentials AND a completed authorization) *before* creating any
    SyncLog row, matching sync_sleeper/sync_espn's fix for the same
    permanently-degraded-banner problem."""
    client_id = _get_setting(db, "yahoo_client_id")
    league_ids = _parse_league_ids(_get_setting(db, "yahoo_league_ids") or "")
    client_secret_encrypted = _get_secret(db, "yahoo_client_secret")
    access_token_encrypted = _get_secret(db, "yahoo_access_token")
    refresh_token_encrypted = _get_secret(db, "yahoo_refresh_token")
    my_guid = _get_setting(db, "yahoo_guid")
    if not (
        client_id
        and league_ids
        and client_secret_encrypted
        and access_token_encrypted
        and refresh_token_encrypted
        and my_guid
    ):
        return

    log = SyncLog(platform="yahoo", started_at=datetime.now(timezone.utc))
    db.add(log)
    db.commit()

    try:
        client_secret = decrypt_value(client_secret_encrypted)
        access_token = decrypt_value(access_token_encrypted)

        expires_at_raw = _get_setting(db, "yahoo_token_expires_at")
        expires_at = (
            datetime.fromisoformat(expires_at_raw) if expires_at_raw else datetime.now(timezone.utc)
        )
        expiring_soon = (expires_at - datetime.now(timezone.utc)).total_seconds() < (
            YAHOO_TOKEN_REFRESH_MARGIN_SECONDS
        )

        if expiring_soon:
            try:
                refresh_token = decrypt_value(refresh_token_encrypted)
                tokens = yahoo_adapter.refresh_access_token(client_id, client_secret, refresh_token)
            except Exception as exc:
                # No amount of retrying fixes an invalid refresh token --
                # this is an expected once-a-season event (Yahoo refresh
                # tokens expire after months of off-season inactivity), not
                # an alarming failure.
                log.success = False
                log.error = f"Yahoo authorization expired -- reconnect in Settings ({exc})"
                log.finished_at = datetime.now(timezone.utc)
                db.commit()
                return

            access_token = tokens["access_token"]
            _set_secret(db, "yahoo_access_token", access_token)
            _set_secret(db, "yahoo_refresh_token", tokens["refresh_token"])
            new_expires_at = datetime.now(timezone.utc) + timedelta(seconds=tokens["expires_in"])
            _set_setting(db, "yahoo_token_expires_at", new_expires_at.isoformat())

        errors: list[str] = []
        for league_id in league_ids:
            try:
                _sync_one_yahoo_league(db, league_id, access_token, my_guid)
                db.commit()
            except Exception as exc:
                _safe_rollback(db)
                logger.exception("yahoo sync: league %s failed", league_id)
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
            logger.exception("yahoo sync: failed to record SyncLog completion")
            _safe_rollback(db)
```

Add two small helpers this task needs, next to the existing `_get_setting`/`_get_secret`:

```python
def _set_setting(db: Session, key: str, value: str) -> None:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def _set_secret(db: Session, key: str, value: str) -> None:
    row = db.query(Secret).filter(Secret.key == key).first()
    encrypted = encrypt_value(value)
    if row is None:
        db.add(Secret(key=key, encrypted_value=encrypted))
    else:
        row.encrypted_value = encrypted
    db.commit()
```

(Place these near the top of `sync.py`, next to `_get_setting`/`_get_secret` — `sync.py` currently only reads settings/secrets; `sync_yahoo` is the first function in this file that needs to write one, since it persists a refreshed token.)

Update `sync_all_platforms`:

```python
def sync_all_platforms() -> None:
    if not _sync_lock.acquire(blocking=False):
        logger.info("sync_all_platforms: a sync is already in progress, skipping this trigger")
        return
    try:
        db = get_sessionmaker()()
        try:
            sync_sleeper(db)
            sync_espn(db)
            sync_yahoo(db)
        finally:
            db.close()
    finally:
        _sync_lock.release()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_yahoo_sync.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the full backend suite**

Run: `python3 -m pytest -q`
Expected: All tests pass, including all existing Sleeper/ESPN sync tests unchanged

- [ ] **Step 6: Commit**

```bash
git add backend/app/sync.py backend/tests/test_yahoo_sync.py
git commit -m "Add sync_yahoo with token-refresh-before-sync"
```

---

## Task 5: Frontend — Yahoo settings section and dashboard team link

**Files:**
- Modify: `frontend/src/api/client.js`
- Modify: `frontend/src/pages/SettingsPage.jsx`
- Modify: `frontend/src/pages/DashboardPage.jsx`

**Interfaces:**
- Consumes: `GET/PUT /settings/yahoo`, `GET /settings/yahoo/authorize-url`, `POST /settings/yahoo/authorize` (Task 3). `platform_team_id`/`platform_league_id` on `GET /leagues` (existing, already used by the ESPN branch).
- Produces: `getYahooSettings()`, `putYahooSettings(leagueIds, clientId, clientSecret)`, `getYahooAuthorizeUrl()`, `submitYahooAuthorizeCode(code)` in `api/client.js`, used only by `SettingsPage.jsx`.

- [ ] **Step 1: Add API client functions**

In `frontend/src/api/client.js`, add after `putEspnSettings`:

```js
export function getYahooSettings() {
  return request('/settings/yahoo')
}

export function putYahooSettings(leagueIds, clientId, clientSecret) {
  const body = { client_id: clientId, league_ids: leagueIds }
  // Same optional-secret contract as ESPN: only send client_secret if the
  // user actually typed something this time.
  if (clientSecret) body.client_secret = clientSecret
  return request('/settings/yahoo', {
    method: 'PUT',
    body: JSON.stringify(body),
  })
}

export function getYahooAuthorizeUrl() {
  return request('/settings/yahoo/authorize-url')
}

export function submitYahooAuthorizeCode(code) {
  return request('/settings/yahoo/authorize', {
    method: 'POST',
    body: JSON.stringify({ code }),
  })
}
```

- [ ] **Step 2: Add the Yahoo section to SettingsPage**

In `frontend/src/pages/SettingsPage.jsx`, update the import block:

```jsx
import { useEffect, useState } from 'react'
import {
  getEspnSettings,
  getSleeperSettings,
  getYahooAuthorizeUrl,
  getYahooSettings,
  putEspnSettings,
  putSleeperSettings,
  putYahooSettings,
  submitYahooAuthorizeCode,
} from '../api/client'
```

Add new state, alongside the existing `espnS2`/`swid`/etc. block:

```jsx
  const [yahooClientId, setYahooClientId] = useState('')
  const [yahooClientSecret, setYahooClientSecret] = useState('')
  const [yahooLeagueIdsText, setYahooLeagueIdsText] = useState('')
  const [yahooClientSecretConfigured, setYahooClientSecretConfigured] = useState(false)
  const [yahooAuthorized, setYahooAuthorized] = useState(false)
  const [yahooCode, setYahooCode] = useState('')
  const [yahooSaved, setYahooSaved] = useState(false)
  const [yahooError, setYahooError] = useState(null)
```

Add a `loadYahooSettings` helper and call it from the existing mount `useEffect` (alongside `getSleeperSettings()`/`getEspnSettings()`):

```jsx
  function loadYahooSettings() {
    return getYahooSettings()
      .then((data) => {
        setYahooClientId(data.client_id)
        setYahooLeagueIdsText(data.league_ids.join(', '))
        setYahooClientSecretConfigured(data.client_secret_configured)
        setYahooAuthorized(data.authorized)
      })
      .catch((err) => {
        setYahooError(
          err.status === 401
            ? 'Your session expired. Log in again to load settings.'
            : 'Could not load settings.',
        )
      })
  }
```

In the existing mount `useEffect`, add `loadYahooSettings()` alongside the existing two calls.

Add the three handlers:

```jsx
  async function handleYahooSubmit(e) {
    e.preventDefault()
    const leagueIds = yahooLeagueIdsText
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    setYahooError(null)
    setYahooSaved(false)
    try {
      await putYahooSettings(leagueIds, yahooClientId, yahooClientSecret)
      setYahooSaved(true)
      setYahooClientSecret('')
      await loadYahooSettings()
    } catch (err) {
      setYahooError(
        err.status === 401
          ? 'Your session expired. Log in again to save settings.'
          : 'Could not save settings.',
      )
    }
  }

  async function handleYahooAuthorizeClick() {
    setYahooError(null)
    try {
      const { url } = await getYahooAuthorizeUrl()
      window.open(url, '_blank', 'noopener,noreferrer')
    } catch (err) {
      setYahooError('Could not start Yahoo authorization. Save your Yahoo credentials first.')
    }
  }

  async function handleYahooCodeSubmit(e) {
    e.preventDefault()
    setYahooError(null)
    try {
      await submitYahooAuthorizeCode(yahooCode)
      setYahooCode('')
      await loadYahooSettings()
    } catch (err) {
      setYahooError('Could not verify that code with Yahoo. Try authorizing again.')
    }
  }
```

Add the new form section, after the existing ESPN `</form>`:

```jsx
      <form className="ld-settings-form" onSubmit={handleYahooSubmit}>
        <label htmlFor="yahooClientId">Yahoo Client ID</label>
        <input
          id="yahooClientId"
          className="ld-input"
          value={yahooClientId}
          onChange={(e) => setYahooClientId(e.target.value)}
        />
        <label htmlFor="yahooClientSecret">
          Yahoo Client Secret{' '}
          {yahooClientSecretConfigured && '(already saved — leave blank to keep it)'}
        </label>
        <input
          id="yahooClientSecret"
          type="password"
          className="ld-input"
          value={yahooClientSecret}
          onChange={(e) => setYahooClientSecret(e.target.value)}
        />
        <label htmlFor="yahooLeagueIds">Yahoo league IDs (comma-separated)</label>
        <input
          id="yahooLeagueIds"
          className="ld-input"
          value={yahooLeagueIdsText}
          onChange={(e) => setYahooLeagueIdsText(e.target.value)}
        />
        <button type="submit" className="ld-button">
          Save
        </button>
        {yahooSaved && <p className="ld-saved">Saved.</p>}

        {yahooClientSecretConfigured && (
          <>
            <p className="ld-saved">{yahooAuthorized ? 'Connected ✓' : 'Not yet connected.'}</p>
            <button type="button" className="ld-button" onClick={handleYahooAuthorizeClick}>
              Authorize with Yahoo
            </button>
            <label htmlFor="yahooCode">Verification code from Yahoo</label>
            <input
              id="yahooCode"
              className="ld-input"
              value={yahooCode}
              onChange={(e) => setYahooCode(e.target.value)}
            />
            <button type="button" className="ld-button" onClick={handleYahooCodeSubmit}>
              Submit code
            </button>
          </>
        )}

        {yahooError && (
          <p role="alert" className="ld-error">
            {yahooError}
          </p>
        )}
      </form>
```

- [ ] **Step 3: Add the Yahoo branch to the dashboard team-link ternary**

In `frontend/src/pages/DashboardPage.jsx`, change:

```jsx
            const teamUrl =
              league.platform === 'sleeper' && league.platform_league_id
                ? `https://sleeper.com/leagues/${league.platform_league_id}/team`
                : league.platform === 'espn' && league.platform_league_id && myTeam.platform_team_id
                  ? `https://fantasy.espn.com/football/team?leagueId=${league.platform_league_id}&teamId=${myTeam.platform_team_id}`
                  : null
```

to:

```jsx
            const teamUrl =
              league.platform === 'sleeper' && league.platform_league_id
                ? `https://sleeper.com/leagues/${league.platform_league_id}/team`
                : league.platform === 'espn' && league.platform_league_id && myTeam.platform_team_id
                  ? `https://fantasy.espn.com/football/team?leagueId=${league.platform_league_id}&teamId=${myTeam.platform_team_id}`
                  : league.platform === 'yahoo' && league.platform_league_id && myTeam.platform_team_id
                    ? `https://football.fantasysports.yahoo.com/f1/${league.platform_league_id}/${myTeam.platform_team_id}`
                    : null
```

- [ ] **Step 4: Lint**

Run: `cd frontend && npm run lint`
Expected: No new errors (the two pre-existing unrelated warnings are fine)

- [ ] **Step 5: Build**

Run: `npm run build`
Expected: Clean production build

- [ ] **Step 6: Manual verification**

Run: `npm run dev` (with the backend running)
Confirm on the Settings page:
- The Yahoo section renders below the ESPN section with its own Save button.
- Saving Client ID/Secret shows "(already saved — leave blank to keep it)" on a second load, and the Authorize/code UI appears once a secret is saved.
- Clicking "Authorize with Yahoo" without real Yahoo API access yet will fail at Yahoo's side (expected — see the spec's blocking prerequisite) but should not crash the page; confirm the error path renders `yahooError` rather than a blank screen.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/client.js frontend/src/pages/SettingsPage.jsx frontend/src/pages/DashboardPage.jsx
git commit -m "Add Yahoo section to Settings page and dashboard team link"
```

---

## Task 6: Manual verification against a real Yahoo league

This task has no automated tests and is **blocked on Yahoo granting API access** to the application created for this feature (see the spec's blocking prerequisite — apply for this as early as possible, ideally before or during Task 1-5 implementation, since the turnaround is unpredictable and outside this project's control). Do not skip this task once access is granted — Task 2's parsing logic is the least-verified part of this entire plan.

- [ ] **Step 1: Confirm Yahoo API access has been granted**

If not yet granted, this task cannot proceed. Note the blocker and move to Task 7 (final review) for everything else; return to this task once access arrives.

- [ ] **Step 2: Configure real credentials**

Start the app, log in, go to Settings, and complete the full Yahoo flow: save Client ID/Secret, click "Authorize with Yahoo," approve access, paste the resulting code.

- [ ] **Step 3: Verify the JSON shape assumption — the highest-priority check**

Before trusting any other part of the sync, temporarily add a debug log (or use a debugger) in `yahoo._get_league_metadata`/`_get_league_teams`/`_get_team_roster`/`_get_scoreboard` to print the raw `response.json()` for a real league, and compare it against the fixture shapes in `test_yahoo_adapter.py`. If the real shape differs from what Task 2's fixtures assumed (this is expected — see Task 2's note), update `_reformat`/`_unwrap_list`/`_navigate` and the parsing functions to match reality, update the fixtures to match, and re-run `pytest tests/test_yahoo_adapter.py -v` until green again. Remove the debug logging before committing.

- [ ] **Step 4: Verify field-level assumptions**

With real data flowing, confirm:
- `editorial_team_abbr` values match the same 32 abbreviations Sleeper's `team` field uses, with no exceptions.
- `selected_position.position` for bench is really `"BN"`, and check whether this league (or a league with an IR spot) uses `"IR"`, `"IR+"`, or `"IR-R"` — add any missing slot label to `IR_SLOTS` in `yahoo.py` and a corresponding test in `test_yahoo_adapter.py`.
- The team page renders correctly and the roster/score/opponent match what Yahoo's own web UI shows for the same league/week.

- [ ] **Step 5: Verify request volume and consider chaining**

Compare the number of HTTP calls per sync (currently `1 metadata + 1 scoreboard + 1 per team`) against Yahoo's actual behavior during a live-window sync (every 60 seconds — see `app/schedule.py`). If Yahoo's API supports fetching all teams' rosters in fewer requests (the spec suggests checking for supported sub-resource chaining), and the per-team-call volume looks risky, implement that optimization now in `yahoo.py` and update `test_yahoo_adapter.py`'s fixtures accordingly. If the volume looks fine, no change needed — note that it was checked.

- [ ] **Step 6: Record findings**

If any step above required a code change, commit it now with a clear message describing what was found (e.g. "Fix Yahoo IR slot label (IR+) not counted as bench" or "Chain Yahoo roster fetches to cut per-sync request count"). If nothing required a change, no commit is needed for this task — the manual check itself is the deliverable.

---

## Task 7: Final review

- [ ] Run the full backend suite one more time: `cd backend && python3 -m pytest -q` — expect all tests passing.
- [ ] Run `cd frontend && npm run build` — expect a clean production build with no errors.
- [ ] Confirm `git log` shows one commit per task above (Tasks 1-5, plus Task 6 only if it produced a fix).
- [ ] Use the superpowers:finishing-a-development-branch skill to close out the branch/worktree per its process (test verification, base-branch confirmation, then the merge/PR/keep-as-is menu).
