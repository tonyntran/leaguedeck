# LeagueDeck — Design Spec

Date: 2026-09-07
Status: Approved, pre-implementation

## Problem

The user plays fantasy football across three platforms — Sleeper, ESPN, and
Yahoo — with multiple leagues on at least one of them. Each platform is a
separate app with its own login, roster view, and lineup-setting UI. No
existing tool provides genuine one-click lineup management across all three;
existing aggregators (Flaim, Fantasy HQ, Draft Sharks League Sync, STACKED)
are read-only — they sync rosters for viewing/advice but explicitly cannot
edit lineups, make trades, or change settings on the source platforms.

LeagueDeck is a personal-use, self-hosted platform that unifies all of the
user's leagues across Sleeper, ESPN, and Yahoo into one dashboard, and
(in a later phase) adds real lineup write-back.

## Prior art in this environment

`~/projects/fantasy-auction-assistant` is an existing project by the same
user: a live auction-draft assistant for Sleeper (with ESPN support) using a
Python/FastAPI backend, a React/Vite/Tailwind/daisyUI dashboard, and a
Chrome extension for live draft interception. LeagueDeck reuses its backend
and dashboard stack for consistency, but is a **separate, standalone
project** — critically, LeagueDeck does **not** use a browser extension.
That was an explicit design decision: LeagueDeck must be its own platform,
not something that depends on a browser add-on being installed/open.

**What's actually reusable from that sibling project** (verified against
its code, not assumed): FastAPI + httpx + pydantic on the backend, and
React + Vite + Tailwind + daisyUI on the frontend. That project has **no**
SQLite/SQLAlchemy, no APScheduler, no docker-compose, and no ESPN HTTP
client of its own — its ESPN support is a Chrome-extension-side DOM scrape,
not an API client, so it isn't reusable for LeagueDeck's cookie-based ESPN
adapter. Its VORP logic also isn't in `engine.py` (that file's
`calculate_vorp` is a thin passthrough) — the real computation is
`_compute_vorps` in `backend/state.py:248`, tightly coupled to that
project's `DraftState` and `config.settings` singleton. Porting it means
extracting that logic out of `DraftState`, not importing a shared package
as-is; see Open Items.

## Non-goals

- Not a commercial product or multi-user SaaS — single user, self-hosted.
- Not a replacement draft-day tool (that's what fantasy-auction-assistant is
  for) — LeagueDeck is for in-season roster management.
- Phase 1 does not write anything back to any platform. It is read + advice
  only.
- No CI-driven automated testing of the Phase 2 browser-automation write
  path — verified manually only, because it is inherently fragile to the
  target sites' UI changes.

## Constraints discovered during platform research

| Platform | Read access | Write access |
|---|---|---|
| Sleeper | Official public API, no auth required | No public write API. Sleeper's own docs state there are no endpoints for modifying lineup/roster. |
| Yahoo | Official API, OAuth 2.0 | Official, supported — `PUT` to the roster resource keyed on `player_key` (e.g. `nfl.p.12345`, not a bare `player_id`) with `selected_position`. |
| ESPN | No official API. Community-reverse-engineered private v3 API, needs `espn_s2`/`SWID` cookies from a logged-in browser session. | Not officially supported; would require automating ESPN's own web UI. |

This asymmetry drives the phased approach below: Yahoo gets full read+write
cleanly through its official API. Sleeper and ESPN get clean reads now, and
UI-automation-based writes only in Phase 2, flagged as higher-risk/fragile.

## Architecture (Phase 1)

```
┌──────────────────────────┐
│   React Dashboard (SPA)   │
└─────────────┬─────────────┘
              │ REST, behind app-level login
┌─────────────▼─────────────────────────────────────┐
│                FastAPI Backend                      │
│  platform adapters:                                  │
│    sleeper.py  — public API, no auth needed          │
│    yahoo.py    — OAuth2, backend-driven consent flow │
│    espn.py     — uses pasted espn_s2/SWID cookies    │
│  scheduler (APScheduler) — polls each platform        │
│  SQLite — leagues, rosters, players; secrets table    │
│    (ESPN cookies, Yahoo tokens) encrypted at rest      │
│  Settings page — paste ESPN cookies + league IDs,      │
│    connect Yahoo, enter Sleeper username/league IDs    │
└─────────────────────────────────────────────────────┘
```

Removed the WebSocket leg from the original sketch — nothing in Phase 1 is
real-time; the dashboard polls its own backend on a normal page-load/refresh
cadence, and the backend's own sync is already interval-based (see Sync,
below).

**App-level access control.** The original draft assumed "reachable only
over Tailscale/LAN" was sufficient protection. It isn't: anything on that
network could hit the API, and once Phase 2 lands, that includes setting
your lineups. LeagueDeck therefore needs its own login (a single-user
password, checked against a hashed value in config — full multi-user auth
would be over-engineering for a one-person tool) gating every API route,
independent of network-level access.

**Secrets at rest.** ESPN cookies and Yahoo refresh tokens are the keys to
the user's real fantasy accounts and must not sit in plaintext in the
SQLite file. Store them in a dedicated `secrets` table, encrypted with a
key derived from a value in `.env` (not committed, not in the SQLite file
itself) — e.g. Fernet symmetric encryption via `cryptography`. The
Settings API must never echo a stored credential back in a response, only
confirm presence/last-updated time.

No browser extension is involved in Phase 1. Only ESPN needs any manual
credential step at all:

- **Sleeper**: user provides their Sleeper username (or user ID) and the
  backend discovers/pulls their leagues directly — fully public API, no
  auth artifacts to manage for reads. (Phase 2 write-back is a different
  story — see below.)
- **Yahoo**: standard OAuth 2.0 authorization-code flow, done once through a
  browser redirect into the backend; backend stores the refresh token
  (encrypted, per above) and polls independently from then on. Yahoo's app
  registration requires an **HTTPS** redirect URI — plain `http://localhost`
  is not accepted, so the self-hosted deployment needs at least a
  self-signed cert or a local HTTPS reverse proxy for the OAuth callback to
  work at all. Refresh tokens can also be revoked/expire on Yahoo's end;
  the sync job needs to detect an auth failure distinctly from a network
  failure and prompt for re-consent rather than silently retrying forever.
- **ESPN**: user manually copies `espn_s2` and `SWID` cookie values from
  their browser's dev tools (this is the standard method community ESPN API
  libraries already document), plus their ESPN league ID(s), and enters them
  into a LeagueDeck settings field once. These cookies are long-lived (weeks
  to months) but will eventually expire and need re-pasting — there is no
  way around this without either storing an ESPN password or building
  browser automation, neither of which is justified for Phase 1 read
  access.

### Sync

An APScheduler job polls each connected platform on an interval and writes
normalized results into SQLite. The dashboard always reads from SQLite, not
live from the platforms, so it loads fast and doesn't hammer any platform's
API on every page view.

Interval is not one-size-fits-all: 15–30 minutes is fine most of the week,
but is too slow to catch a late-breaking inactive right before kickoff on
game day. Exact scheduling (e.g. tightening the interval automatically
during each platform's live game windows) is deferred to implementation
planning (see Open Items), but the requirement itself belongs in this spec:
polling frequency must be able to increase around kickoff, not stay fixed
all week.

**Sync failures must surface, not fail silently.** `SyncLog` records each
sync attempt, but a log nobody looks at doesn't help on a Sunday morning
when ESPN cookies quietly expired. The dashboard must show a visible
degraded-sync indicator (e.g. "ESPN last synced 6 hours ago — check your
cookies in Settings") whenever a platform's last successful sync is older
than some threshold, rather than only recording it in a table the user has
to think to check.

### Data model & cross-platform player identity

Each platform has its own player ID scheme. Rather than fuzzy name-matching
(error-prone across defenses, suffixes, and nicknames), LeagueDeck uses the
maintained **nflverse/ffverse player ID crosswalk** (a CSV mapping
`sleeper_id`/`espn_id`/`yahoo_id`/`gsis_id` per player) as the canonical join
key. A player not found in the crosswalk (rare — mostly D/STs or
just-signed players) falls back to fuzzy name+team+position matching.

Core internal models: `League`, `Team`, `Roster`, `Player`, `Matchup`,
`SyncLog`. Each platform adapter's job is solely to normalize that
platform's raw response into these shared models — no platform-specific
logic exists outside the adapter.

"Current NFL week" is resolved from each platform's own API response
(Sleeper, ESPN, and Yahoo all return the current scoring period/week
directly) rather than by maintaining a separate NFL schedule/calendar
source — one less thing to keep in sync.

### Weekly projections (needed for start/sit and waiver features)

Features 3 and 4 below need weekly (not season-long) player projections,
which the sibling project doesn't have — its `projections.py` only merges
hand-curated season-long draft CSVs (`ProjectedPoints`/`BaselineAAV`/`Tier`),
not weekly numbers. Two sources, usable together:

- **Each platform's own weekly projected points**, returned alongside
  roster/matchup data by ESPN's and Yahoo's APIs for that team's own
  players — free, already-fetched, no new dependency, but Sleeper's public
  API does not include projections at all.
- A **free third-party weekly projections source** (e.g. data available via
  the `nflreadr`/`ffverse` ecosystem, or FantasyPros' public rankings) to
  fill the Sleeper gap and to have one normalized number when comparing
  players across platforms. Exact source and refresh cadence is deferred to
  implementation planning, but the spec must not leave this fully
  unresolved — Sleeper specifically requires an external projections source
  since it has none of its own.

## Dashboard features (Phase 1, in build order)

1. **Unified roster + matchup view** — every team the user owns, across
   every connected league/platform, in one screen: current
   starters/bench, this week's opponent and score. This is the foundation
   every other feature is built on top of.
2. **Injury/news alerts** — a single feed surfacing any rostered player
   across any team who is questionable/out/on a bye this week.
3. **Start/sit recommendations** — a standalone lineup-optimization score
   inspired by the VORP approach in `fantasy-auction-assistant`'s
   `_compute_vorps` (originally built for auction values, not directly
   reusable — see Prior art and Open Items), driven by the weekly
   projections described above, suggesting the best starting lineup per
   team.
4. **Waiver wire / trending pickups** — combines Sleeper's trending-adds
   endpoint with each team's open roster spots and positional needs to
   surface add/drop suggestions.

## Phase 2 (sketched, not built now): one-click lineup writes

```
┌─────────────▼─────────────────────────────────────┐
│         Playwright write-worker (separate process)  │
│  headless browser + a stored login session for       │
│  whichever platform(s) need UI-driven writes          │
│  navigates to the real lineup page, performs the      │
│  actual UI actions to set your lineup                │
└─────────────────────────────────────────────────────┘
```

- **Yahoo**: add a `PUT` call to `yahoo.py` against the official roster
  endpoint (keyed on `player_key`, see above), using the OAuth token already
  obtained in Phase 1. Low risk, officially supported.
- **ESPN**: ESPN's own web UI submits lineup changes to the same private
  JSON API already used for reads, using the same `espn_s2`/`SWID` cookies
  already stored in Phase 1. **Try a direct authenticated POST to that
  transactions endpoint first** — it needs no new credential collection and
  no headless-browser infrastructure, and carries the same ToS posture as
  the read path. Fall back to Playwright UI-automation only if that write
  endpoint can't be reverse-engineered reliably.
- **Sleeper**: Sleeper's public API has no write endpoint, and — unlike
  ESPN — Phase 1 collects no Sleeper credential at all (its reads are fully
  public). Phase 2 write-back for Sleeper therefore requires a **new**
  credential-collection step at that time: the user manually captures their
  Sleeper web-app session cookie the same way they do for ESPN. That cookie
  is what the Playwright worker loads to drive Sleeper's actual lineup UI.
  This is called out explicitly now so Phase 2 planning doesn't assume a
  credential that Phase 1 never collects.

**Risk callout, carried forward explicitly so it isn't a surprise later:**
driving a headless browser to take actions against ESPN's/Sleeper's
consumer web app is further into ToS-gray territory than read-only cookie
use, because it's now performing actions, not just fetching data. For a
single personal account this is a low practical risk, but it is a real one
(could theoretically trigger anti-bot detection or account review) and is
a deliberate tradeoff being made for personal convenience, not something to
scale beyond this one account.

## Deployment

Self-hosted via docker-compose (backend + frontend containers, SQLite on a
mounted volume) running on the user's own machine/NAS. Reached from a phone
via Tailscale or LAN, behind the app-level login described above.
`docker-compose up` is close to the entire install step, with one wrinkle:
Yahoo's OAuth callback requires HTTPS, so the compose setup needs a
self-signed cert or a local HTTPS-terminating reverse proxy in front of the
backend for that flow to work. No cloud hosting, no ongoing hosting cost,
no secrets leaving the user's own hardware (and secrets that do live on
that hardware are encrypted at rest, not plaintext).

## Testing

- Per-adapter unit tests (mocked platform responses) verifying normalization
  into the shared `League`/`Team`/`Roster`/`Player`/`Matchup` models.
- A pytest suite for the lineup-scoring engine, following the same pattern
  as `fantasy-auction-assistant/backend/tests/`.
- No CI coverage of the Phase 2 Playwright write path — verified manually
  against the user's real accounts only, since it depends on live
  third-party UI that isn't under LeagueDeck's control.

## Open items deferred to implementation planning

- Exact SQLite schema / migration approach.
- Specific polling intervals per platform, including how the "tighten
  polling near kickoff" requirement is implemented in APScheduler (balance
  freshness vs. rate limits — Sleeper docs suggest staying well under 1000
  req/min; ESPN and Yahoo have no published personal-use limits but should
  be polled conservatively).
- The start/sit engine is a standalone port inspired by `_compute_vorps` in
  `fantasy-auction-assistant/backend/state.py:248`, not a shared-package
  import — that logic is coupled to the sibling project's `DraftState` and
  `config.settings` singleton and isn't extractable as-is. Exact scoring
  formula for season-long (vs. auction-day) VORP is a planning-time detail.
- Exact weekly-projections source and refresh cadence for the Sleeper gap
  (see Weekly projections, above).
- Whether direct-POST ESPN lineup writes are feasible to reverse-engineer
  reliably, or whether Phase 2 needs Playwright for ESPN too, not just
  Sleeper.
- Concrete secrets-encryption implementation (key management/rotation for
  the Fernet key in `.env`) and the app-login mechanism's exact form
  (session cookie vs. token).
