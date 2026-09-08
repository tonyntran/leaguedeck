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
  compare players across platforms. **Named cost of this deferral (added
  after review):** the same real NFL player/team can render slightly
  differently across a Sleeper card and an ESPN card until both are
  normalized to a shared vocabulary (see "Numeric ID mapping," below, for
  the specific defense-naming case this already requires handling). This
  is a real, user-visible inconsistency being accepted as the cost of not
  building the crosswalk yet, not an oversight.
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
- **Player names are embedded directly in the roster response**
  (`playerPoolEntry.player.fullName`), so no separate bulk player-lookup
  call is needed the way Sleeper requires — this part of the original claim
  holds. **Correction from an earlier draft of this spec:** position,
  pro-team, and lineup-slot are NOT embedded as readable strings — they
  come back as ESPN-internal numeric IDs (`defaultPositionId`,
  `proTeamId`, `lineupSlotId`). The adapter needs static lookup tables to
  turn these into the strings the rest of the app expects — see "Numeric
  ID mapping," below. "No ID mapping needed at all" was wrong; "no
  *external bulk API call* needed" is the accurate claim.
- League IDs persist across seasons; season is a separate URL segment (see
  Season handling, below).

## Numeric ID mapping (new section, added after review)

ESPN's roster response encodes position, pro team, and lineup slot as
integers, not the strings this app already uses everywhere (Sleeper
already normalizes to strings like `"QB"`, `"DEF"`, team abbreviations
like `"KC"`). The adapter needs two static translation tables:

- **Position** (`defaultPositionId` → string): `1: "QB"`, `2: "RB"`,
  `3: "WR"`, `4: "TE"`, `5: "K"`, `16: "DEF"`. **Must map ESPN's defense
  slot to `"DEF"`, not ESPN's own `"D/ST"` label** — the frontend's
  `POSITION_ORDER` array (`DashboardPage.jsx`) is Sleeper's vocabulary,
  and an unmapped `"D/ST"` string would silently sort to the bottom of
  every roster instead of its correct spot, with no error to indicate why.
- **Pro team** (`proTeamId` → abbreviation): a static 32-team lookup table
  (plus `0` for free agent/no team), matching the same 2-3 letter
  abbreviation convention Sleeper's `team` field already uses (e.g. `"KC"`,
  not ESPN's own team-name strings).
- **Starter vs. bench** (`is_starter`): derived from `lineupSlotId` — not
  a starter if the slot is Bench (`20`) or IR (`21`). Verify against the
  real league during implementation whether it defines any other
  non-starting slot IDs (e.g. a taxi squad) beyond these two well-documented
  ones — don't assume the list above is exhaustive without checking a real
  roster response.

## Per-player object contract (new section, added after review)

The "exact same shape" claim above covers the team-level keys, but the
thing the frontend actually reads inside `roster_json` is the per-player
object, which must match Sleeper's exactly:
`{"player_id": str, "name": str, "position": str, "team": str | None,
"is_starter": bool}` — `position`/`team` are the mapped strings from
"Numeric ID mapping," not ESPN's raw values.

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
  endpoint above and normalizing into the shared shape. `season` is stored
  as a string on the `League` row (matching Sleeper's convention and the
  column's actual type), so the adapter returns `str(season)` in its
  output even though it accepts an `int` — cast at the boundary, not left
  ambiguous.
  - **Determining "my team" (`is_mine`), corrected from an earlier draft**:
    nothing in ESPN's response "marks" which team is yours. The adapter
    must match the already-stored `swid` against each team's owner GUID(s)
    in the `mTeam` view's `owners`/`primaryOwner` field. GUID comparison
    needs normalization (case and brace-wrapping can differ between what
    the user pastes and what ESPN returns) — don't do a naive string `==`.
  - Views: `mTeam`, `mRoster`, `mMatchup`, `mSettings` combined in one
    request per the API research above. **Known risk, not fully resolved
    at spec level**: community reports suggest combined multi-view
    requests can occasionally return less data than the union of calling
    each view separately, and that live/in-progress week scores may need
    `mMatchupScore` rather than `mMatchup`. Verify against the real league
    during implementation; be ready to fall back to two requests per league
    if the combined call doesn't reliably return everything needed. This
    is a real risk to the "one HTTP call per league" claim above, not a
    settled fact.
- **`app/crypto.py`**: no changes — `encrypt_value`/`decrypt_value` already
  exist and are used for the first time by this feature.
- **`app/models.py`**: no schema changes — the `Secret` table (key,
  encrypted_value) already exists from the foundation plan, unused until
  now.
- **`app/routers/settings.py`**: new `GET/PUT /settings/espn`.
  - `PUT` accepts `espn_s2`, `swid`, `league_ids`. **Different contract
    from Sleeper's PUT, corrected after review — this is not "the same
    rule," it's a genuinely different requirement**: because `GET
    /settings/espn` returns booleans instead of echoing the real values
    (see below), a naive symmetric PUT (always overwrite both fields) lets
    a user who only wants to update `league_ids` submit blank
    `espn_s2`/`swid` fields and silently wipe their already-stored
    credentials. `espn_s2` and `swid` must be **optional** on `PUT`: omitted
    or blank means "leave the currently stored value unchanged," only a
    non-blank value overwrites. `league_ids` is not secret and can keep
    Sleeper's always-overwrite behavior.
  - `GET` returns `league_ids` plus **booleans** `espn_s2_configured` /
    `swid_configured` — never the actual secret values. This is the same
    display rule the original design spec laid down explicitly for this
    exact reason (`Settings API must never echo a stored credential back`)
    — only the display rule is shared with Sleeper; the PUT semantics
    above are not, per the correction just above.
- **`app/sync.py`**: new `sync_espn(db)`. **Reframed after review**: this
  is a new, separate ~30-40 line function following the same *shape* as
  `sync_sleeper(db)` (per-league `try`/`except` isolation, one `SyncLog`
  row per attempt, partial-success semantics) — it is not a refactor into
  shared code, and "structurally parallel" shouldn't be read as "cheap to
  add" for that reason. Accepted duplication for two platforms; revisit
  extracting a shared per-league-sync helper only if Yahoo repeats the same
  shape a third time (rule of three), not before.
  - **Critical difference from Sleeper's sync, found in review**: ESPN
    setup is genuinely optional — many users may never configure it, unlike
    Sleeper which every current user configures during initial setup.
    `sync_sleeper` currently creates its `SyncLog` row *before* checking
    whether Sleeper is configured, then records a `success=False` "not
    configured" failure if it isn't. Copying that pattern into
    `sync_espn` would mean: every sync cycle (startup + every 20 minutes,
    forever) writes a new failed `SyncLog` row for a platform the user may
    have no intention of ever using, which (a) makes `/sync-status` report
    ESPN as permanently degraded — `isDegraded()` treats
    `last_success === false` as always-alert, so a Sleeper-only user would
    see a red "espn last synced N minutes ago — check Settings" banner
    forever — and (b) grows `SyncLog` with garbage rows indefinitely.
    **Fix, specific to `sync_espn`**: check whether ESPN is configured at
    all (an `espn_league_ids` `AppSetting` is present and non-empty, and
    both secrets exist) *before* creating any `SyncLog` row. If
    unconfigured, return immediately with no log entry — `/sync-status`
    should simply omit `"espn"` from its platform list until the user
    actually sets it up, not report it as failing. This is a deliberate,
    ESPN-specific behavior, not a claim that Sleeper's existing behavior is
    wrong — Sleeper's window of being "unconfigured" is brief (once, at
    first install) where ESPN's may be permanent for many users.
  - Decrypts `espn_s2`/`swid` from `Secret` via `crypto.decrypt_value`
    before calling the adapter. Called from `sync_all_platforms()`
    alongside `sync_sleeper(db)`.
  - **Edge case, named but not solved differently**: if an ESPN league ID
    doesn't exist yet for the computed season (e.g. a brand-new season
    where the league hasn't rolled over), ESPN's API 404s for that league.
    The existing per-league `try`/`except` isolation already degrades this
    to one failed league within the sync rather than crashing anything —
    no special-case handling needed beyond what per-league isolation
    already provides.
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
  `putEspnSettings` must let the cookie fields be omitted (matching the
  backend's optional-secret PUT contract above) — the Settings form should
  only send `espn_s2`/`swid` if the user actually typed something into
  those fields this time, not blank/placeholder values, or a
  league-IDs-only edit would wipe stored credentials from the frontend
  side even with the backend fix in place.
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
  should also fail this test). Also: `PUT` with `league_ids` only (cookie
  fields omitted) must leave previously-stored `espn_s2`/`swid` unchanged —
  this directly tests the blank-overwrite bug found in review, not just
  the happy path.
- Sync tests: per-league isolation (one bad ESPN league doesn't block
  others or Sleeper), mirroring `test_sync.py`'s existing Sleeper
  coverage. Also: `sync_espn(db)` with no ESPN settings configured writes
  zero `SyncLog` rows — this directly tests the permanent-false-degraded-
  banner bug found in review, not just "doesn't crash."
- Manual verification against the user's real ESPN league, the same way
  Sleeper was verified — this spec's author (the user) has confirmed real
  ESPN league access is available for this.

## Non-goals

- Player-ID crosswalk (see Scope decisions, above).
- Lineup writes (ESPN's private API has no documented write endpoint;
  this remains Phase 2 territory per the original design spec).
- A dedicated season-selection UI.
