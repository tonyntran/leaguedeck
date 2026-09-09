# Yahoo Adapter — Design Spec

Date: 2026-09-08
Status: Approved, pre-implementation (revised after independent review)

## Problem

LeagueDeck currently supports Sleeper and ESPN. This spec adds Yahoo as the
third platform, following the same pattern both prior platforms established:
a new adapter producing the shared `League`/`Team` shape, a new sync
function wired into `sync_all_platforms()`, and a new Settings section —
supporting multiple Yahoo leagues the same way Sleeper and ESPN already do
(comma-separated league IDs, one dashboard card per league).

Yahoo's fantasy API is officially documented (unlike ESPN's reverse-engineered
private API), but requires OAuth2 rather than a username (Sleeper) or two
pasted cookies (ESPN) — the credential story is genuinely different in kind,
not just in the specific fields collected. Its JSON is also structurally
messier than either prior platform's, being a mechanical conversion of an
XML API rather than a native JSON one — see "Yahoo's JSON shape," below.

**Blocking prerequisite, not a code concern:** as of this writing, Yahoo
requires every application to be manually reviewed and approved by Yahoo's
Fantasy Sports team before it can call the API at all — no published
turnaround time. Apply for access **now**, in parallel with implementation,
since it is the one part of this project that cannot be sped up by writing
code. Manual verification against a real league (see Testing, below) is
blocked until access is granted.

## Scope decisions made with the user

- **OAuth via the "oob" (out-of-band) flow, not a real HTTPS redirect
  callback.** Yahoo's authorization code flow supports `redirect_uri=oob`
  for applications with no web server to receive a redirect: the user
  approves access in a Yahoo-hosted tab, Yahoo shows a one-time
  verification code on-screen, and the user pastes that code back into
  LeagueDeck. This was chosen specifically to avoid standing up an HTTPS
  listener (self-signed cert, new port, browser security-warning UX) purely
  for this one platform's callback — a real cost that a single-user,
  self-hosted app has no other reason to pay. Confirmed directly against
  Yahoo's current developer documentation (not deprecated, still an
  officially supported option) before this decision was made.
- **Hand-roll the JSON unpacking, don't take `yfpy` as a dependency.**
  Yahoo's `?format=json` output is a mechanical XML→JSON conversion (see
  "Yahoo's JSON shape," below) that the community library `yfpy` already
  parses correctly and completely. Taking it as a dependency was considered
  and rejected: it would only save the unpacking work specifically (its
  object model still needs adapting into LeagueDeck's plain-dict
  `normalize_league` contract either way), and it breaks the
  zero-third-party-platform-library pattern Sleeper and ESPN both
  established — both call `httpx` directly with no wrapper. The accepted
  cost is real: Yahoo's raw shape needs more unpacking code than ESPN's
  clean nested JSON did, with real risk of a subtle bug in that layer.
- **Yahoo league IDs are not stable across seasons — accept annual
  re-entry rather than engineering around it.** Unlike Sleeper and ESPN,
  Yahoo mints a new `league_id` each season for the same real league
  (linked to the prior season's ID via a `renew` field in league metadata).
  The chosen fix is the simplest one: keep the same plain-league-ID UX as
  the other two platforms, and document that the user needs to update the
  ID in Settings once a year at Yahoo's season rollover. Auto-following the
  `renew` chain was considered and rejected as engineering a rare (once a
  year), low-cost inconvenience into a stateful, harder-to-debug feature
  for a limitation this spec would rather just name. See "Season rollover
  is a known annual step," below, for what this looks like in practice.
- **No per-player actual/projected points for Yahoo in this pass.** Sleeper
  and ESPN both got this as a *second*, later feature built once their base
  adapters existed and were verified against real data — Yahoo follows the
  same incremental path rather than trying to ship everything in one spec.
  Yahoo's own API already exposes `team_points`/`team_projected_points`
  fully pre-computed for the league's real scoring settings (better footing
  than Sleeper's PPR-format approximation), so this should be a cheap
  fast-follow once the base adapter is real and tested. **Named cost:**
  every Yahoo roster row will render with an empty points column
  (`actual_points`/`projected_points` both `None`, per the shared per-player
  contract) while Sleeper and ESPN rows show real numbers — a real,
  user-visible inconsistency accepted the same way the ESPN spec accepted
  cross-platform naming inconsistencies from deferring the player-ID
  crosswalk.
- **No lineup writes.** Still read-only everywhere, matching Sleeper and
  ESPN — and matching Yahoo's own API, which is documented as read-only
  with no write access currently offered at all, so this isn't purely a
  choice this app is making.
- **Each self-hosted instance's owner creates their own Yahoo app.** Yahoo
  issues a Client ID and Client Secret per registered application on Yahoo's
  developer site — this is a one-time setup step for the user, analogous to
  pulling ESPN's `espn_s2`/`SWID` cookies from DevTools, just on Yahoo's
  side instead of the browser's, and gated on the approval process named
  above.

## Yahoo API research (verified before writing this spec)

- Base URL for the fantasy API: `https://fantasysports.yahooapis.com/fantasy/v2/`.
  Requests append `?format=json` to get JSON instead of Yahoo's default XML
  — this parameter is community-verified (widely used, not in Yahoo's own
  reference docs, which show XML exclusively), consistent with the general
  reliability level of Yahoo's public documentation for this API.
- **OAuth endpoints** (both on a separate host from the fantasy API):
  - Authorization: `https://api.login.yahoo.com/oauth2/request_auth`
    — params `client_id`, `redirect_uri=oob`, `response_type=code`. The user
    is sent here in a new browser tab; no server-side handling needed until
    they come back with a code.
  - Token exchange: `https://api.login.yahoo.com/oauth2/get_token` — POST
    with `client_id`/`client_secret` sent as an HTTP Basic Auth header
    (base64 of `client_id:client_secret`, per RFC 2617 — **not** as body
    parameters), plus body params `redirect_uri=oob`, `code`,
    `grant_type=authorization_code`. Response JSON has `access_token`,
    `refresh_token`, `expires_in` (seconds; verified as 3600 — access
    tokens are short-lived, unlike ESPN's cookies or Sleeper's lack of
    auth), and `xoauth_yahoo_guid` — the authenticated user's own Yahoo
    GUID, used below for `is_mine` matching.
  - Refresh: **same token endpoint, same Basic Auth header, AND the same
    `redirect_uri=oob` parameter as the initial exchange** (easy to miss —
    Yahoo's own example refresh request includes `redirect_uri`; omitting
    it risks an `invalid_request` that would misleadingly look like an
    expired refresh token), with `grant_type=refresh_token` and
    `refresh_token=<stored value>` instead of `code`. Returns a new
    `access_token` (and sometimes a new `refresh_token` — always persist
    whatever comes back, never assume the refresh token is stable across
    refreshes).
  - **Refresh-token expiry is an expected seasonal event, not a rare edge
    case.** Yahoo refresh tokens expire after a period of account
    inactivity (commonly cited around 60 days). A fantasy dashboard sits
    essentially idle from February to August — about six months. **Every
    user should expect to need to re-run the authorize flow at the start of
    every season**, not just on rare account-security events. The sync
    error message (see `app/sync.py`, below) should be written with this
    in mind, not as a scary rare-failure message.
- **League keys**: a full league key is `<game_key>.l.<league_id>`. Rather
  than resolving a numeric `game_id` via a separate `/game/nfl/metadata`
  call, the literal string `nfl` is itself a valid `game_key` that Yahoo
  resolves server-side to whichever NFL fantasy game is currently active
  (confirmed in Yahoo's own reference docs, which give `nfl.l.1000` as a
  valid example league key). **This means no separate game-ID-resolution
  call or function is needed at all** — the adapter builds
  `f"nfl.l.{league_id}"` directly. This does not change the season-rollover
  problem above (the `league_id` the user must supply still changes yearly)
  — it only removes an unnecessary extra API call for something Yahoo
  already resolves given the literal sport code.
- **Yahoo's JSON shape is a mechanical XML→JSON conversion, not a clean
  native JSON API — this is the single most important research finding in
  this spec and the adapter cannot be written without accounting for it.**
  A list of N items renders not as a JSON array but as an object with
  string keys `"0"`, `"1"`, ..., `"N-1"` plus a sibling `"count"` key giving
  N — e.g. a league's teams collection looks like
  `{"0": {"team": [...]}, "1": {"team": [...]}, "count": 2}`, not
  `[{"team": [...]}, {"team": [...]}]`. The same shape appears for a
  single, non-repeating value too (XML has no distinct "this is definitely
  one item" representation), so a nested object can appear wrapped as
  `{"0": {...}}` with no `"count"` sibling at all. **This shape is expected
  based on the well-documented behavior of Yahoo's XML-derived JSON output,
  but has not been confirmed against a live response in this codebase** (no
  live testing was possible before Yahoo API access was granted — see the
  blocking prerequisite above). **Verifying the exact shape of a real
  response against a real league is the first, most important item in
  Testing, below** — every other verification item in this spec depends on
  parsing succeeding at all.
- **Per-player fields** (from a team's roster response,
  `/team/{team_key}/roster;week={week}`, itself subject to the indexed-list
  shape above for the entries list): `player_id`, `name.full`,
  `display_position`, `editorial_team_abbr` (pro team — likely already
  matches the 2-3 letter convention Sleeper's `team` field uses directly,
  since Yahoo, like Sleeper, uses standard broadcast-style abbreviations
  rather than ESPN's internal numeric IDs; **verify against a real Yahoo
  league during implementation** rather than assume every one of the 32
  matches with zero exceptions), and each roster entry's
  `selected_position.position` (the lineup slot — `"BN"` for bench;
  **verify whether Yahoo has a separate IR-equivalent slot label during
  implementation** — some league configurations are known to use `"IR"`
  and/or `"IR+"`/`"IR-R"` variants, the way ESPN has `IR` distinct from
  `BN`, so don't assume `"BN"` is the only non-starting value).
- **Type coercion: every value in Yahoo's JSON arrives as a string,
  including numbers and booleans — this is the second most important
  research finding, and the adapter must not skip it.** `team_points.total`
  is the string `"105.42"`, not the float `105.42`; `week`/`current_week`
  are strings like `"5"`; boolean-flavored fields like a team's ownership
  flag (see `is_mine`, below) are the strings `"1"`/`"0"`, not `True`/
  `False`. **The string `"0"` is truthy in Python** — a naive
  `if team.get(some_flag):` check would treat every team as true. The
  adapter must explicitly cast: `float(...)` for points, `int(...)` for
  week, and explicit string comparison (`== "1"`, never a bare truthiness
  check) for any boolean-flavored field. `season` stays a string
  throughout (matching the `League.season` column's actual type and the
  convention `sleeper.py`/`espn.py` already follow), so no cast is needed
  there — but it must be read from wherever the league metadata response
  actually puts it and not assumed to already be the right type by luck.
- **Determining "my team" (`is_mine`): match GUIDs, the same pattern ESPN
  already uses, not a team-level "is this mine" flag.** The OAuth token
  response's `xoauth_yahoo_guid` field (see OAuth endpoints, above) is the
  authenticated user's own Yahoo GUID. Each team's manager information
  (itself subject to the indexed-list unpacking above) includes a `guid`
  per manager. `is_mine` is `any(manager_guid == my_guid for manager_guid
  in this_team's_manager_guids)` — mirroring `espn.py`'s
  `_normalize_guid`/owners-list comparison pattern exactly, including
  normalizing case if Yahoo's GUID casing turns out to vary the way ESPN's
  did (**verify during implementation**). A team-level ownership flag
  (something like `is_owned_by_current_login`) may also exist on some
  response shapes, but it is not confirmed to be part of Yahoo's officially
  documented team resource, and GUID matching is the same proven approach
  this codebase already trusts — use it as the primary method, not a
  flag whose presence isn't guaranteed.
- **Team score fields**: `team_points.total` (actual) and
  `team_projected_points.total` (projected) both come back fully computed
  for that league's real scoring settings — no per-league format-picking
  approximation needed the way Sleeper's `pts_ppr`/`pts_half_ppr`/`pts_std`
  selection requires. (Projected points are a non-goal for this pass per
  Scope decisions above, but noted here since it removes a whole class of
  complexity Sleeper's adapter has to carry, for whenever that fast-follow
  happens. Remember both values are strings — see Type coercion, above.)
- **Matchup/opponent data**: comes from a `scoreboard` object containing a
  `matchups` collection (indexed-list shape, see above), each with a
  `teams` collection of exactly two entries — structurally similar to
  ESPN's `schedule` array (a list of games, each identifying its two
  teams), not Sleeper's roster-id-keyed matchup rows. The adapter pairs
  teams the same way `espn.py`'s `_team_score` does: scan for the game
  containing this team, read the other team's key as the opponent.
- **Request volume during a live sync window is a real constraint, not
  just a Sleeper/ESPN concern.** Fetching one Yahoo league costs roughly
  `1 league-metadata call + 1 scoreboard call + 1 call per team` (rosters
  are fetched per team) — around 14 calls for a 12-team league, versus
  Sleeper's 4-per-league and ESPN's 1-per-league (ESPN's adapter
  deliberately combines five views into a single request for exactly this
  reason). The existing live-window fast-sync job calls `sync_all_
  platforms()` every 60 seconds during a likely-live window (`sync.py`),
  which would mean roughly 840 requests/hour per Yahoo league during
  games. Yahoo's terms of service mention an undisclosed per-application
  rate limit, and community reports describe throttling surfacing as a
  bare dropped connection rather than a clean HTTP 429 — which would land
  in this adapter's per-league exception handling as an opaque connection
  error rather than a recognizable "rate limited" message. **The adapter
  should fetch all teams' rosters in as few requests as possible** — Yahoo
  supports resource sub-collection chaining (e.g. requesting a league's
  teams together with their rosters in one call, rather than one request
  per team) — the exact chained-request URL shape should be confirmed
  against a real response during implementation and is the second
  most important thing to verify, after the base JSON shape itself.

Sources consulted: Yahoo's own current developer documentation
(`developer.yahoo.com/oauth2/guide/flows_authcode/`, `sports.yahoo.com/
developer/docs/`) for the OAuth flow, endpoints, and league key format, and
`yfpy` (`github.com/uberfastman/yfpy`) — specifically its `utils.py`
unpacking helpers, not just its post-unpacking `models.py` — as the source
for what Yahoo's *raw* JSON shape actually looks like before any
normalization.

## Architecture

```
Frontend                    Backend                         Yahoo
   │                            │                              │
   │ PUT /settings/yahoo        │  client_id, league_ids        │
   │  {client_id,               │  -> AppSetting (not secret)   │
   │   client_secret,           │  client_secret -> Secret      │
   │   league_ids}              │  (encrypted via crypto.py)    │
   ├───────────────────────────►│                                │
   │                            │                                │
   │ GET /settings/yahoo/       │  build request_auth URL        │
   │  authorize-url             │  (client_id + redirect_uri=oob)│
   │◄───────────────────────────┤                                │
   │  {url}                     │                                │
   │                            │                                │
   │ (user opens url in a new tab, approves, gets a code from Yahoo)
   │                            │                                │
   │ POST /settings/yahoo/      │  POST get_token with the code  │
   │  authorize {code}          │  (Basic Auth: client_id:secret)│
   ├───────────────────────────►├───────────────────────────────►│
   │                            │◄─── access_token, refresh_token,│
   │                            │      xoauth_yahoo_guid          │
   │                            │  encrypt token pair -> Secret;  │
   │                            │  guid + expiry -> AppSetting    │
   │◄───────────────────────────┤                                │
   │  {ok: true}                │                                │
   │                            │                                │
   │                            │  (on sync) refresh access_token │
   │                            │  if needed, yahoo.normalize_    │
   │                            │  league(league_id, access_token,│
   │                            │  my_guid)                       │
   │                            │◄───────────────────  {teams...} │
   │                            │  same shared shape Sleeper/ESPN │
   │                            │  produce; write League/Team rows│
```

`app/adapters/yahoo.py` produces the **exact same shape**
`sleeper.normalize_league`/`espn.normalize_league` already produce:
`{"platform", "platform_league_id", "name", "season", "teams": [{...}]}`
with each team having `platform_team_id, name, is_mine, roster_json,
points_for, opponent_name, opponent_points, week`, and each roster player
having `player_id, name, position, team, is_starter, actual_points,
projected_points` — the last two always `None` for Yahoo (see Scope
decisions). This is why `sync.py`'s per-league commit loop, `app/routers/
leagues.py`, and `DashboardPage.jsx`'s rendering need no changes to consume
Yahoo data once it exists as `League`/`Team` rows.

## Backend changes

- **`app/adapters/yahoo.py`** (new file):
  - `_unwrap_list(obj: dict | list) -> list` — the one shared helper this
    adapter needs for Yahoo's indexed-object list shape (see "Yahoo's JSON
    shape," above): if `obj` is already a `list`, return it unchanged; if
    it's a `dict`, return `[v for k, v in obj.items() if k != "count"]`. A
    genuinely single, unwrapped value can be recovered by taking element
    `[0]` of the result at the call site — this one small helper covers
    both the "list of N" and "wrapped single value" cases described above,
    applied only at the specific points this adapter actually descends
    into a Yahoo collection (teams, roster entries, matchups, a matchup's
    two teams, a team's managers) — not a fully generic recursive
    normalizer the way `yfpy`'s `utils.py` implements, since this adapter
    only needs to unpack the shapes it actually touches.
  - `get_authorize_url(client_id: str) -> str` — builds the
    `request_auth` URL with `redirect_uri=oob`.
  - `exchange_code_for_tokens(client_id: str, client_secret: str, code: str) -> dict`
    — POSTs to `get_token` with the Basic Auth header, returns
    `{"access_token": str, "refresh_token": str, "expires_in": int,
    "yahoo_guid": str}` (the last renamed from Yahoo's `xoauth_yahoo_guid`
    at the adapter boundary, matching this codebase's convention of
    normalizing external field names rather than carrying them through
    verbatim).
  - `refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict`
    — same endpoint, `grant_type=refresh_token` **and** `redirect_uri=oob`
    (see OAuth endpoints, above — easy to omit by mistake). Returns the
    same shape as `exchange_code_for_tokens`; callers must persist
    whatever `refresh_token` comes back.
  - `normalize_league(league_id: str, access_token: str, my_guid: str) -> dict`
    — builds the league key as `f"nfl.l.{league_id}"` (no separate
    game-ID-resolution call — see League keys, above), fetches league
    metadata, team rosters (in as few requests as the confirmed chaining
    shape allows — see Request volume, above), and scoreboard/matchups,
    and normalizes into the shared shape using `_unwrap_list` at each
    collection boundary and explicit type coercion per the Type coercion
    section above. Mirrors `espn.normalize_league`'s internal structure
    (resolve week/season first, then loop teams building roster + score +
    opponent). `is_mine` per team is determined by matching `my_guid`
    against that team's unpacked manager GUIDs (see `is_mine`, above) —
    never a team-level ownership flag.
  - Every roster player dict includes `"actual_points": None,
    "projected_points": None"` explicitly, for shape parity with Sleeper/
    ESPN's per-player contract (see Scope decisions' named cost, above) —
    not simply omitting the keys.
- **`app/crypto.py`**: no changes — existing `encrypt_value`/`decrypt_value`
  used for the two genuinely secret values (`client_secret`, and the token
  pair) — see the corrected storage split below.
- **`app/models.py`**: no schema changes — `Secret`/`AppSetting` already
  support arbitrary keys.
- **`app/routers/settings.py`**: new endpoints, all under `/settings/yahoo`.
  **Storage split, corrected from an earlier draft of this spec**:
  `client_id` is not a secret (it's an identifier, not a password — the
  same category as Sleeper's username) and belongs in `AppSetting` where it
  can be echoed back to the user so they can see which Yahoo app is
  configured; only `client_secret` and the token pair are genuinely
  sensitive and belong in the encrypted `Secret` table. Storage keys, named
  explicitly: `AppSetting` gets `yahoo_client_id`, `yahoo_league_ids`,
  `yahoo_guid`, `yahoo_token_expires_at`; `Secret` gets `yahoo_client_secret`,
  `yahoo_access_token`, `yahoo_refresh_token` — six keys total, not the
  "four secrets" an earlier draft of this spec undercounted.
  - `GET /settings/yahoo` — returns `client_id` (the real value — it's not
    secret), `league_ids`, and **booleans** `client_secret_configured`,
    `authorized` (true once an access/refresh token pair exists) — never
    the actual `client_secret` or token values, matching the display rule
    already established for ESPN. The settings test for this endpoint must
    assert the raw `client_secret` and token strings appear **nowhere** in
    the response body, not just that they're absent under their own field
    names (mirroring the ESPN spec's own leak test).
  - `PUT /settings/yahoo` — accepts `client_id` (always overwrites, it's
    not secret), `client_secret` (**optional**, same blank-means-unchanged
    contract as ESPN's `espn_s2`/`swid`), and `league_ids` (always
    overwrites). This step does **not** touch tokens — changing the league
    list or rotating the app's client secret shouldn't silently invalidate
    an existing authorization.
  - `GET /settings/yahoo/authorize-url` — reads the stored `client_id`,
    returns `{"url": yahoo.get_authorize_url(client_id)}`. 400s if no
    `client_id` is stored yet. (`GET`, not `POST` — this has no side
    effects, it only builds a URL from already-stored data.)
  - `POST /settings/yahoo/authorize` — accepts `{"code": str}`, reads the
    stored `client_id`/`client_secret`, calls
    `yahoo.exchange_code_for_tokens(...)`, encrypts and stores
    `yahoo_access_token`/`yahoo_refresh_token` in `Secret`, and stores
    `yahoo_guid` plus the expiry timestamp (`yahoo_token_expires_at`, as
    `datetime.now(timezone.utc) + timedelta(seconds=expires_in)`,
    serialized via `.isoformat()`, matching the existing serialization
    convention `/sync-status` already uses for `last_success_at`) in
    `AppSetting`. Returns `{"ok": true}` on success, 400 with a clear error
    if the code exchange fails (e.g. an expired or already-used code —
    Yahoo's codes are single-use and short-lived). **Never log the pasted
    code, the token response body, or the Basic Auth header** — this
    endpoint's error path must not echo Yahoo's raw error body back to the
    client or into application logs, since a token response is a live
    credential, unlike a generic error message.
- **`app/sync.py`**: new `sync_yahoo(db)`, mirroring `sync_sleeper`/
  `sync_espn`'s shape (per-league try/except isolation, one `SyncLog` row
  per attempt, zero `SyncLog` rows when unconfigured — Yahoo is exactly as
  optional as ESPN, so it gets the same unconfigured-skip fix that both
  other platforms now have).
  - **Token refresh, specific to Yahoo**: before each sync attempt, check
    the stored `yahoo_token_expires_at` against the current time (with a
    safety margin — refresh if expiring within, say, 5 minutes) and call
    `refresh_access_token` if needed, persisting the new access token (and
    refresh token, if Yahoo rotated it) before calling `normalize_league`.
    If the refresh itself fails, record that as this sync's failure with a
    clear, expected-not-alarming error message (something like "Yahoo
    authorization expired — this happens after the off-season; reconnect
    in Settings"), since no amount of retrying fixes an invalid refresh
    token and — per the Refresh-token expiry research above — this is a
    normal once-a-season occurrence, not a sign of a bug.
  - **Concurrency note**: this is the first sync path that *writes* to the
    `Secret`/`AppSetting` tables rather than only reading them (the
    existing `_get_secret`/`_get_setting` helpers in `sync.py` are
    read-only). The existing `_sync_lock` (see `sync.py`) already prevents
    two sync attempts from writing concurrently with each other, so a
    token refresh during `sync_yahoo` is safe with respect to the
    live-window job and the 20-minute baseline job racing each other. It
    does **not** cover a token refresh happening concurrently with a user
    submitting `POST /settings/yahoo/authorize` from the request thread at
    the same moment — an accepted, low-probability gap for a single-user
    self-hosted app, not a new lock to add.
  - Called from `sync_all_platforms()` alongside `sync_sleeper(db)` and
    `sync_espn(db)`.
- **No changes needed** to `app/routers/leagues.py`'s `GET /leagues` or
  `GET /sync-status` — both already operate on `League`/`Team`/`SyncLog`
  generically across platforms, the same reason ESPN needed no changes
  there either.

## Season rollover is a known annual step

Per the Scope decision above: when Yahoo rolls a league over to a new
season, its `league_id` changes for the same real league (linked via a
`renew` field the adapter does not follow). In practice, once a year, the
user's stored Yahoo league ID(s) in Settings will stop resolving to the
league they expect — the sync for that league will start failing (Yahoo
returns an auth/permission-style error for a league key the user isn't
actually a member of, since the old ID may now belong to a different real
league in the new season's namespace, or to nothing at all). The fix is
simply looking up the new season's league ID from Yahoo's own UI and
updating Settings — the same one-time-per-season action as, say, updating
a bookmark. This is named here explicitly, the same way the ESPN spec named
its own accepted costs, rather than being a silent trap the user discovers
on their own.

## Frontend changes

- **`pages/SettingsPage.jsx`**: new Yahoo section, structurally a bit
  different from Sleeper/ESPN's single form since the OAuth handshake is
  multi-step:
  1. `client_id` (plain text — not secret) / `client_secret` (password-style)
     inputs + `league_ids` input + a "Save" button — calls the new
     `PUT /settings/yahoo`, same save/error pattern as the other two
     sections.
  2. An "Authorize with Yahoo" button, enabled once `client_secret_configured`
     is true (from `GET /settings/yahoo`) — calls
     `GET /settings/yahoo/authorize-url` and opens the returned URL in a
     new tab (`window.open`).
  3. A verification-code input + "Submit" button — shown whenever
     `client_secret_configured` is true (**not** only in an ephemeral
     "flow just started" state that a page reload would lose, stranding
     the user with a code and nowhere to paste it), calling
     `POST /settings/yahoo/authorize` with the pasted code, then
     re-fetching `GET /settings/yahoo` to update the `authorized` status
     shown to the user (e.g. "Connected ✓" once true).
- **`api/client.js`**: `getYahooSettings()`,
  `putYahooSettings(leagueIds, clientId, clientSecret)` (league IDs first,
  matching `putEspnSettings(leagueIds, espnS2, swid)`'s established
  argument order; omitting a blank `clientSecret` per the optional-secret
  contract), `getYahooAuthorizeUrl()`, `submitYahooAuthorizeCode(code)`.
- **`pages/DashboardPage.jsx`**: one small addition, not "no changes" — the
  existing per-platform team-header-link ternary (added for Sleeper, then
  ESPN) gets a third branch for Yahoo:
  `https://football.fantasysports.yahoo.com/f1/{platform_league_id}/{platform_team_id}`,
  using the same `platform_team_id` field the ESPN branch already relies
  on. Costs nothing extra once the adapter populates that field, and
  keeps Yahoo cards consistent with the other two platforms rather than
  being the only one with an unclickable header. Waiver Wire stays gated
  to `platform === 'sleeper'` (unchanged; Yahoo has no waiver-wire feature
  built, same as ESPN); the roster rows' points column will render empty
  for Yahoo per the named cost above — no code change needed for that, it
  falls out of `actual_points`/`projected_points` both being `None`.

## Testing

- Adapter tests mocking `httpx.get`/`httpx.post`, matching the ESPN adapter
  test pattern (fake responses keyed by URL/method), covering:
  `_unwrap_list` against both the indexed-dict-with-count shape and a
  plain list (pass-through); `get_authorize_url` builds the expected URL;
  `exchange_code_for_tokens` sends the Basic Auth header correctly and
  parses the token response including `yahoo_guid`; `refresh_access_token`
  does the same with `grant_type=refresh_token` **and** asserts
  `redirect_uri=oob` is present in the request; `normalize_league` builds
  the correct league key (`nfl.l.{league_id}`, no separate game-lookup
  call) and produces the shared shape correctly (name, roster, position,
  team, starter/bench, score, opponent, `actual_points`/`projected_points`
  both `None`) from representative fixture responses built in Yahoo's
  actual indexed-list shape (not a simplified plain-list fixture that
  would pass without exercising `_unwrap_list` at all); explicit tests for
  the type-coercion behavior (a string `"105.42"` becomes the float
  `105.42`; a string `"0"` ownership flag is correctly treated as false,
  not truthy) — this exact bug class is named in the research above and
  deserves its own test, not just incidental coverage.
- Settings router tests: round-trip save/load for `client_id`/`league_ids`
  against the corrected storage split (client_id echoed, client_secret
  never echoed), the optional-secret PUT contract (blank/omitted
  `client_secret` preserves the stored value, same test shape as ESPN's),
  the leak test asserting the raw `client_secret`/token strings appear
  nowhere in any response body, the authorize-url endpoint building the
  right URL from a stored client_id (and 400s without one), and the
  authorize endpoint exchanging a code and storing tokens plus `yahoo_guid`
  (mocking `yahoo.exchange_code_for_tokens`) and 400ing on a failed
  exchange.
- Sync tests: per-league isolation (one bad Yahoo league doesn't block
  others or the other platforms), zero `SyncLog` rows when unconfigured
  (mirroring the existing ESPN test), and specifically tests for the
  token-refresh-before-sync behavior — "token still valid, no refresh
  call," "token expiring soon, refresh called and new token persisted,"
  and "refresh itself fails, sync records the expected-seasonal-event
  re-authorize message rather than a generic failure."
- **Manual verification against the user's real Yahoo league is more
  load-bearing for this platform than it was for Sleeper or ESPN**, and is
  blocked on Yahoo's application approval (see the blocking prerequisite
  at the top of this spec). In priority order once access is granted:
  1. Confirm the actual raw JSON shape of a real response matches "Yahoo's
     JSON shape" above (indexed-object-with-count for lists) — if this is
     wrong, nothing else in the adapter can work, so confirm it first,
     before writing normalize_league's body, not after.
  2. Confirm whether the roster-fetching request can be chained to avoid
     one HTTP call per team (see Request volume, above) — this determines
     whether the live-window fast-sync cadence is safe to enable for Yahoo
     without a separate rate-limit conversation.
  3. Confirm `editorial_team_abbr`/`display_position` need no translation
     table, and confirm the exact bench/IR slot label(s) in
     `selected_position.position`.
  4. Confirm GUID matching (`xoauth_yahoo_guid` vs. each team's manager
     `guid`) correctly identifies the user's own team, including whether
     case normalization is needed the way ESPN's GUIDs required it.

## Non-goals

- Per-player actual/projected points for Yahoo (see Scope decisions —
  planned as a fast-follow once the base adapter is verified against real
  data).
- Lineup writes (also not offered by Yahoo's API at all currently).
- A real HTTPS redirect callback (the oob flow is the whole point of
  avoiding this — see Scope decisions).
- Automatically following Yahoo's season-to-season `league_id` `renew`
  chain (see Scope decisions and "Season rollover is a known annual step,"
  above — accepted as a once-a-year manual Settings update instead).
- Yahoo-specific live-scoring cadence work beyond confirming request
  volume fits the existing cadence (see Request volume and Testing item 2,
  above) — once `sync_yahoo` exists and is wired into `sync_all_platforms`,
  the existing live-window fast-sync job (built for Sleeper/ESPN) covers it
  automatically with no Yahoo-specific *code* changes needed, provided the
  request-chaining verification above confirms the volume is actually safe.
