# ESPN Adapter — Design Spec

Date: 2026-09-07
Status: Approved, pre-implementation

## Problem

LeagueDeck currently supports Sleeper only. The original design spec
(`2026-09-07-leaguedeck-design.md`) scoped ESPN as a follow-on plan: manual
cookie-based read access (no official API exists), reconciling with the
same shared `League`/`Team` shape Sleeper already populates. This spec adds
ESPN as LeagueDeck's second platform, supporting multiple ESPN leagues the
same way Sleeper already supports multiple leagues (comma-separated league
IDs, one dashboard card per league).

## Scope decisions made with the user

- **No player-ID crosswalk.** The original spec planned an
  nflverse/ffverse crosswalk to reconcile the same real player's different
  IDs across platforms. Nothing in the current codebase compares players
  *across* platforms — every feature (dashboard cards, waiver wire) is
  scoped to a single league on a single platform. Building the crosswalk
  now would be speculative work for a feature that doesn't exist yet.
  ESPN's adapter resolves player names from ESPN's own API response
  directly (see below — no separate bulk lookup is even needed, unlike
  Sleeper). Revisit the crosswalk only when/if a feature actually needs to
  compare players across platforms.
- **Settings page extended, not a separate page.** ESPN credentials/league
  IDs are added as a new section on the existing Settings page, matching
  Sleeper's section.
- **Real ESPN league available for verification.** Unlike a from-scratch
  platform, this can be tested against real data the same way Sleeper was.

## ESPN API research (verified before writing this spec)

- No official API. Base URL for the private v3 API (2018+ seasons):
  `https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league_id}`
- Auth: two cookies, `espn_s2` and `SWID`, obtained by the user manually
  from their browser's DevTools (Application → Cookies →
  `fantasy.espn.com`) — sent as `cookies={"espn_s2": ..., "SWID": ...}` on
  the request, not as headers.
- Views can be combined in one request via repeated `view=` query params:
  `?view=mTeam&view=mRoster&view=mMatchup&view=mSettings` — one HTTP call
  per league covers team names, rosters, matchups, and league settings
  (current scoring period / week). This is fewer calls than Sleeper needs
  (which requires 4 separate endpoint hits per league).
- **Player names/positions/pro-teams are embedded directly in the roster
  response** (each roster entry's `playerPoolEntry.player` object) — ESPN
  does not require a separate bulk player-lookup call the way Sleeper does.
  This is what makes skipping the crosswalk practical: ESPN's adapter needs
  no external ID-to-name mapping at all.
- League IDs persist across seasons; season is a separate URL segment (see
  Season handling, below).

Sources consulted: community-maintained ESPN API documentation/libraries
(`espn-api`, `espnff`), which is the same reverse-engineering the original
design spec already relied on for its ESPN research — this spec re-verified
the current endpoint shape and view names rather than assuming they hadn't
drifted.

## Architecture

```
Frontend                Backend                          ESPN private API
   │                        │                                     │
   │ PUT /settings/espn     │                                     │
   │  {espn_s2, swid,       │  encrypt espn_s2/swid via           │
   │   league_ids}          │  crypto.py -> Secret table          │
   ├───────────────────────►│  league_ids -> AppSetting            │
   │                        │                                     │
   │                        │  (on sync) decrypt cookies,          │
   │                        │  espn.normalize_league(              │
   │                        │    league_id, season,                │
   │                        │    espn_s2, swid)  ──────────────────►
   │                        │◄────────────────────  {teams, ...}   │
   │                        │  same shared shape Sleeper produces  │
   │                        │  write League/Team rows exactly as   │
   │                        │  sync_sleeper already does           │
```

`app/adapters/espn.py` produces the **exact same shape**
`app.adapters.sleeper.normalize_league` already produces:
`{"platform", "platform_league_id", "name", "season", "teams": [{...}]}`
with each team having `platform_team_id, name, is_mine, roster_json,
points_for, opponent_name, opponent_points, week`. This is why `sync.py`,
`app/routers/leagues.py`, and the frontend need no changes to consume ESPN
data once it exists as `League`/`Team` rows — they already operate on
`League.platform` generically.

## Backend changes

- **`app/adapters/espn.py`** (new file): `normalize_league(league_id: str,
  season: int, espn_s2: str, swid: str) -> dict`, calling the combined-view
  endpoint above and normalizing into the shared shape. Determining "my
  team" (`is_mine`): ESPN's `mTeam` view includes each team's owner info;
  the private API does not require a separate "who am I" call the way
  Sleeper's `get_user_id` does — the authenticated cookies themselves
  identify the requesting member, and ESPN's response marks which team
  belongs to them. (Exact field name is an implementation-plan detail, not
  a spec-level decision — verified against the real league during
  implementation, not assumed here.)
- **`app/crypto.py`**: no changes — `encrypt_value`/`decrypt_value` already
  exist and are used for the first time by this feature.
- **`app/models.py`**: no schema changes — the `Secret` table (key,
  encrypted_value) already exists from the foundation plan, unused until
  now.
- **`app/routers/settings.py`**: new `GET/PUT /settings/espn`.
  - `PUT` accepts `espn_s2`, `swid`, `league_ids` (list of strings, same
    shape as Sleeper's). Encrypts `espn_s2`/`swid` via `crypto.py` before
    writing to `Secret` (keys `"espn_s2"`, `"espn_swid"`); writes
    `league_ids` to `AppSetting` (key `"espn_league_ids"`, comma-joined,
    same convention as Sleeper's).
  - `GET` returns `league_ids` plus **booleans** `espn_s2_configured` /
    `swid_configured` — never the actual secret values. This is the same
    rule the original design spec laid down explicitly for this exact
    reason (`Settings API must never echo a stored credential back`).
- **`app/sync.py`**: new `sync_espn(db)`, structurally parallel to
  `sync_sleeper(db)` — same per-league `try`/`except` isolation (one bad
  league or an expired-cookie auth failure must not block the others or
  Sleeper's sync), same `SyncLog` row per attempt with `platform="espn"`,
  same partial-success semantics. Decrypts `espn_s2`/`swid` from `Secret`
  via `crypto.decrypt_value` before calling the adapter. Called from
  `sync_all_platforms()` alongside `sync_sleeper(db)`.
- **No changes needed** to `app/routers/leagues.py`'s `GET /leagues` or
  `GET /sync-status` — both already operate on `League`/`Team`/`SyncLog`
  generically across platforms.

## Season handling

ESPN league IDs are stable across years; season is a separate parameter
the adapter must supply. Default: current calendar year, falling back to
the previous year before March 1 (NFL season roughly spans
September–February, so a request in January still means "last season" in
ESPN's terms). No user-facing season setting for v1 — this is a
computed default, re-evaluated on every sync.

## Frontend changes

- **`pages/SettingsPage.jsx`**: new ESPN section — `espn_s2` and `swid`
  inputs (password-style, since these are effectively session credentials,
  even though they're not literally an account password), a
  comma-separated league IDs input, matching Sleeper's section layout and
  save/error handling pattern.
- **`api/client.js`**: `getEspnSettings()` / `putEspnSettings(...)`.
- **`pages/DashboardPage.jsx`**: **required fix, not optional polish** —
  the Waiver Wire section is currently fetched/rendered unconditionally for
  every league. `GET /leagues/{id}/waiver-wire` 404s for any
  `league.platform != "sleeper"`, which the frontend's existing `.catch()`
  turns into an empty array, rendering "No trending players available
  right now." for ESPN leagues — which reads as "checked, found none"
  rather than "not supported for this platform." Once ESPN leagues exist
  this is a real, user-visible incorrect statement, not a hypothetical
  edge case. Fix: only fetch and render the Waiver Wire section when
  `league.platform === "sleeper"`.

## Testing

- Adapter tests mocking `httpx.get`, matching the Sleeper adapter test
  pattern (fake responses keyed by URL, including the combined `view=`
  query string).
- Settings router tests: round-trip save/load, and explicitly asserting
  `GET /settings/espn` never contains the raw `espn_s2`/`swid` values
  anywhere in the response body (not just checking the intended field
  names — a regression that leaked a secret into an unexpected field
  should also fail this test).
- Sync tests: per-league isolation (one bad ESPN league doesn't block
  others or Sleeper), mirroring `test_sync.py`'s existing Sleeper
  coverage.
- Manual verification against the user's real ESPN league, the same way
  Sleeper was verified — this spec's author (the user) has confirmed real
  ESPN league access is available for this.

## Non-goals

- Player-ID crosswalk (see Scope decisions, above).
- Lineup writes (ESPN's private API has no documented write endpoint;
  this remains Phase 2 territory per the original design spec).
- A dedicated season-selection UI.
