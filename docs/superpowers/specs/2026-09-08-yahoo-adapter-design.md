# Yahoo Adapter — Design Spec

Date: 2026-09-08
Status: Approved, pre-implementation

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
not just in the specific fields collected.

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
- **No per-player actual/projected points for Yahoo in this pass.** Sleeper
  and ESPN both got this as a *second*, later feature built once their base
  adapters existed and were verified against real data — Yahoo follows the
  same incremental path rather than trying to ship everything in one spec.
  Yahoo's own API already exposes `team_points`/`team_projected_points`
  fully pre-computed for the league's real scoring settings (better
  footing than Sleeper's PPR-format approximation), so this should be a
  cheap fast-follow once the base adapter is real and tested.
- **No lineup writes.** Still read-only everywhere, matching Sleeper and
  ESPN.
- **Each self-hosted instance's owner creates their own Yahoo app.** Yahoo
  issues a Client ID and Client Secret per registered application on Yahoo's
  developer site — this is a one-time setup step for the user, analogous to
  pulling ESPN's `espn_s2`/`SWID` cookies from DevTools, just on Yahoo's
  side instead of the browser's.

## Yahoo API research (verified before writing this spec)

- Base URL for the fantasy API: `https://fantasysports.yahooapis.com/fantasy/v2/`.
  All requests append `?format=json` to get JSON instead of Yahoo's default
  XML.
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
    auth).
  - Refresh: same token endpoint and same Basic Auth header, with
    `grant_type=refresh_token` and `refresh_token=<stored value>` instead
    of `code`. Returns a new `access_token` (and sometimes a new
    `refresh_token` — always persist whatever comes back, never assume the
    refresh token is stable across refreshes).
- **League keys are season-specific, unlike Sleeper/ESPN's stable IDs.** A
  full league key is `<game_id>.l.<league_id>` — `game_id` is a numeric ID
  Yahoo assigns per sport per season (changes every year) and `league_id`
  is the stable per-league number the user actually sees in Yahoo's UI.
  Querying `/game/nfl/metadata` (the literal string `nfl`, not a numeric
  game ID) resolves to whatever NFL fantasy game is currently active and
  returns that season's real numeric `game_id` — the adapter resolves this
  itself so the user only ever enters the plain league ID they see in
  Yahoo's UI, matching Sleeper/ESPN's UX.
- **Per-player fields** (from a team's roster response,
  `/team/{team_key}/roster;week={week}`): `player_id`, `name.full`,
  `display_position`, `editorial_team_abbr` (pro team — likely already
  matches the 2-3 letter convention Sleeper's `team` field uses directly,
  since Yahoo, like Sleeper, uses standard broadcast-style abbreviations
  rather than ESPN's internal numeric IDs; **verify against a real Yahoo
  league during implementation** rather than assume every one of the 32
  matches with zero exceptions), and each roster entry's
  `selected_position.position` (the lineup slot — `"BN"` for bench;
  **verify whether Yahoo has a separate IR-equivalent slot label during
  implementation**, the way ESPN has `IR` distinct from `BN`).
- **Team score fields**: `team_points.total` (actual) and
  `team_projected_points.total` (projected) both come back fully computed
  for that league's real scoring settings — no per-league format-picking
  approximation needed the way Sleeper's `pts_ppr`/`pts_half_ppr`/`pts_std`
  selection requires. (Projected points are a non-goal for this pass per
  Scope decisions above, but noted here since it removes a whole class of
  complexity Sleeper's adapter has to carry.)
- **Matchup/opponent data**: comes from a `scoreboard` object containing a
  `matchups` list, each with a `teams` array — structurally similar to
  ESPN's `schedule` array (a list of games, each identifying its two
  teams), not Sleeper's roster-id-keyed matchup rows. The adapter pairs
  teams the same way `espn.py`'s `_team_score` does: scan for the game
  containing this team, read the other team's key as the opponent.

Sources consulted: Yahoo's own current developer documentation
(`developer.yahoo.com/oauth2/guide/flows_authcode/`) for the OAuth flow and
endpoints, and `yfpy` (`github.com/uberfastman/yfpy`), the most-used Python
wrapper for this API, for the actual field names and URL patterns its
`query.py`/`models.py` use against the real API.

## Architecture

```
Frontend                    Backend                         Yahoo
   │                            │                              │
   │ PUT /settings/yahoo        │  encrypt client_id/secret     │
   │  {client_id, client_secret,│  via crypto.py -> Secret table│
   │   league_ids}              │  league_ids -> AppSetting     │
   ├───────────────────────────►│                                │
   │                            │                                │
   │ POST /settings/yahoo/      │  build request_auth URL        │
   │  authorize-url             │  (client_id + redirect_uri=oob)│
   │◄───────────────────────────┤                                │
   │  {url}                     │                                │
   │                            │                                │
   │ (user opens url in a new tab, approves, gets a code from Yahoo)
   │                            │                                │
   │ POST /settings/yahoo/      │  POST get_token with the code  │
   │  authorize {code}          │  (Basic Auth: client_id:secret)│
   ├───────────────────────────►├───────────────────────────────►│
   │                            │◄─── access_token, refresh_token│
   │                            │  encrypt both -> Secret table   │
   │◄───────────────────────────┤                                │
   │  {ok: true}                │                                │
   │                            │                                │
   │                            │  (on sync) refresh access_token │
   │                            │  if needed, yahoo.normalize_    │
   │                            │  league(league_id, access_token)│
   │                            │◄───────────────────  {teams...} │
   │                            │  same shared shape Sleeper/ESPN │
   │                            │  produce; write League/Team rows│
```

`app/adapters/yahoo.py` produces the **exact same shape**
`sleeper.normalize_league`/`espn.normalize_league` already produce:
`{"platform", "platform_league_id", "name", "season", "teams": [{...}]}`
with each team having `platform_team_id, name, is_mine, roster_json,
points_for, opponent_name, opponent_points, week`. This is why `sync.py`'s
per-league commit loop, `app/routers/leagues.py`, and the frontend need no
changes to consume Yahoo data once it exists as `League`/`Team` rows.

## Backend changes

- **`app/adapters/yahoo.py`** (new file):
  - `get_authorize_url(client_id: str) -> str` — builds the
    `request_auth` URL with `redirect_uri=oob`.
  - `exchange_code_for_tokens(client_id: str, client_secret: str, code: str) -> dict`
    — POSTs to `get_token` with the Basic Auth header, returns
    `{"access_token": str, "refresh_token": str, "expires_in": int}`.
  - `refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict`
    — same endpoint, `grant_type=refresh_token`. Returns the same shape;
    callers must persist whatever `refresh_token` comes back (Yahoo may
    rotate it).
  - `get_current_game_id(access_token: str) -> str` — queries
    `/game/nfl/metadata`, returns the current season's numeric game ID.
  - `normalize_league(league_id: str, game_id: str, access_token: str) -> dict`
    — builds the league key (`f"{game_id}.l.{league_id}"`), fetches league
    metadata, team rosters, and scoreboard/matchups, and normalizes into
    the shared shape. Mirrors `espn.normalize_league`'s internal structure
    (resolve week/season first, then loop teams building roster + score +
    opponent).
  - **Determining "my team" (`is_mine`)**: Yahoo's league-teams response
    includes an `is_owned_by_current_login` (or equivalently-purposed) flag
    per team when the request is made with the authenticated user's own
    access token — **verify the exact field name against a real league
    during implementation**; this is more direct than ESPN's GUID-matching
    since Yahoo already knows which team belongs to the authenticated user
    without needing a separate identifier comparison.
  - **Numeric ID mapping**: unlike ESPN, Yahoo's `display_position` and
    `editorial_team_abbr` are already strings, not numeric IDs — no
    translation table is expected to be needed, but **this must be
    verified against a real league during implementation** (per the API
    research section above) before assuming zero exceptions, the same way
    the ESPN spec initially assumed no ID mapping was needed and was wrong
    about that.
- **`app/crypto.py`**: no changes — existing `encrypt_value`/`decrypt_value`
  used for all four new secrets, same as ESPN's two.
- **`app/models.py`**: no schema changes — `Secret`/`AppSetting` already
  support arbitrary keys.
- **`app/routers/settings.py`**: new endpoints, all under `/settings/yahoo`:
  - `GET /settings/yahoo` — returns `league_ids` plus **booleans**
    `client_configured`, `authorized` (true once an access/refresh token
    pair exists) — never the actual secret values, matching the display
    rule already established for ESPN.
  - `PUT /settings/yahoo` — accepts `client_id`, `client_secret` (both
    **optional**, same blank-means-unchanged contract as ESPN's
    `espn_s2`/`swid`) and `league_ids` (always overwrites). This step does
    **not** touch tokens — changing the league list or rotating the app's
    client secret shouldn't silently invalidate an existing authorization.
  - `POST /settings/yahoo/authorize-url` — reads the stored `client_id`,
    returns `{"url": yahoo.get_authorize_url(client_id)}`. 400s if no
    `client_id` is stored yet.
  - `POST /settings/yahoo/authorize` — accepts `{"code": str}`, reads the
    stored `client_id`/`client_secret`, calls
    `yahoo.exchange_code_for_tokens(...)`, encrypts and stores
    `yahoo_access_token`/`yahoo_refresh_token` plus the expiry timestamp
    (`yahoo_token_expires_at`, stored as an `AppSetting` — not secret, just
    a timestamp — as `datetime.now(timezone.utc) + timedelta(seconds=expires_in)`,
    serialized via `.isoformat()`, matching the existing serialization
    convention `/sync-status` already uses for `last_success_at`) in
    `Secret`/`AppSetting`. Returns `{"ok": true}` on
    success, 400 with a clear error if the code exchange fails (e.g. an
    expired or already-used code — Yahoo's codes are single-use and
    short-lived).
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
    If the refresh itself fails (refresh token revoked or expired — Yahoo
    refresh tokens can expire after a long period of inactivity, unlike
    ESPN's cookies which only expire when the user's browser session
    does), record that as this sync's failure with a clear error message
    (something like "Yahoo authorization expired — reconnect in
    Settings"), since no amount of retrying fixes an invalid refresh
    token; the user must re-run the authorize flow.
  - Called from `sync_all_platforms()` alongside `sync_sleeper(db)` and
    `sync_espn(db)`.
- **No changes needed** to `app/routers/leagues.py`'s `GET /leagues` or
  `GET /sync-status` — both already operate on `League`/`Team`/`SyncLog`
  generically across platforms, the same reason ESPN needed no changes
  there either.

## Frontend changes

- **`pages/SettingsPage.jsx`**: new Yahoo section, structurally a bit
  different from Sleeper/ESPN's single form since the OAuth handshake is
  multi-step:
  1. `client_id`/`client_secret` inputs (password-style) + `league_ids`
     input + a "Save" button — calls the new `PUT /settings/yahoo`, same
     save/error pattern as the other two sections.
  2. An "Authorize with Yahoo" button, enabled once `client_configured` is
     true (from `GET /settings/yahoo`) — calls
     `POST /settings/yahoo/authorize-url` and opens the returned URL in a
     new tab (`window.open`).
  3. A verification-code input + "Submit" button that appears once the
     authorize flow has been started — calls
     `POST /settings/yahoo/authorize` with the pasted code, then re-fetches
     `GET /settings/yahoo` to update the `authorized` status shown to the
     user (e.g. "Connected ✓" once true).
- **`api/client.js`**: `getYahooSettings()`, `putYahooSettings(clientId, clientSecret, leagueIds)`
  (omitting blank credential fields, same contract as `putEspnSettings`),
  `getYahooAuthorizeUrl()`, `submitYahooAuthorizeCode(code)`.
- **`pages/DashboardPage.jsx`**: no changes needed — Yahoo leagues render
  through the same generic `league.platform`-agnostic card rendering
  already in place. Waiver Wire stays gated to `platform === 'sleeper'`
  (unchanged; Yahoo has no waiver-wire feature built, same as ESPN).

## Testing

- Adapter tests mocking `httpx.get`/`httpx.post`, matching the ESPN
  adapter test pattern (fake responses keyed by URL/method), covering:
  `get_authorize_url` builds the expected URL; `exchange_code_for_tokens`
  sends the Basic Auth header correctly and parses the token response;
  `refresh_access_token` does the same with `grant_type=refresh_token`;
  `get_current_game_id` parses the game metadata response;
  `normalize_league` builds the correct league key from game_id + league_id
  and produces the shared shape correctly (name, roster, position, team,
  starter/bench, score, opponent) from representative fixture responses.
- Settings router tests: round-trip save/load for `client_id`/`league_ids`
  (never echoing `client_secret`), the optional-secret PUT contract
  (blank/omitted credential fields preserve stored values, same test shape
  as ESPN's), the authorize-url endpoint building the right URL from a
  stored client_id, and the authorize endpoint exchanging a code and
  storing tokens (mocking `yahoo.exchange_code_for_tokens`).
- Sync tests: per-league isolation (one bad Yahoo league doesn't block
  others or the other platforms), zero `SyncLog` rows when unconfigured
  (mirroring the existing ESPN test), and specifically a test for the
  token-refresh-before-sync behavior — both the "token still valid, no
  refresh call" and "token expiring soon, refresh called and new token
  persisted" paths, plus "refresh itself fails, sync records a clear
  re-authorize error rather than a generic failure."
- Manual verification against the user's real Yahoo league, the same way
  Sleeper and ESPN were verified — needed specifically to confirm the two
  items flagged as unverified in the API research section:
  `editorial_team_abbr`/`display_position` really do need no translation
  table, and the exact field name Yahoo uses to mark the authenticated
  user's own team.

## Non-goals

- Per-player actual/projected points for Yahoo (see Scope decisions —
  planned as a fast-follow once the base adapter is verified against real
  data).
- Lineup writes.
- A real HTTPS redirect callback (the oob flow is the whole point of
  avoiding this — see Scope decisions).
- Yahoo-specific live-scoring cadence work — once `sync_yahoo` exists and
  is wired into `sync_all_platforms`, the existing live-window fast-sync
  job (built for Sleeper/ESPN) covers it automatically with no Yahoo-specific
  changes needed, since that job just calls `sync_all_platforms()` as a
  whole.
