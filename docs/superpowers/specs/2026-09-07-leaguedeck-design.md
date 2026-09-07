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
| Yahoo | Official API, OAuth 2.0 | Official, supported — `PUT` to the roster resource with `player_id`/`selected_position`. |
| ESPN | No official API. Community-reverse-engineered private v3 API, needs `espn_s2`/`SWID` cookies from a logged-in browser session. | Not officially supported; would require automating ESPN's own web UI. |

This asymmetry drives the phased approach below: Yahoo gets full read+write
cleanly through its official API. Sleeper and ESPN get clean reads now, and
UI-automation-based writes only in Phase 2, flagged as higher-risk/fragile.

## Architecture (Phase 1)

```
┌──────────────────────────┐
│   React Dashboard (SPA)   │
└─────────────┬─────────────┘
              │ REST / WebSocket
┌─────────────▼─────────────────────────────────────┐
│                FastAPI Backend                      │
│  platform adapters:                                  │
│    sleeper.py  — public API, no auth needed          │
│    yahoo.py    — OAuth2, backend-driven consent flow │
│    espn.py     — uses pasted espn_s2/SWID cookies    │
│  scheduler (APScheduler) — polls each platform        │
│  SQLite — leagues, rosters, players, tokens/cookies   │
│  Settings page — paste ESPN cookies, connect Yahoo,   │
│    enter Sleeper username/league IDs                  │
└─────────────────────────────────────────────────────┘
```

No browser extension is involved in Phase 1. Only ESPN needs any manual
credential step at all:

- **Sleeper**: user provides their Sleeper username (or user ID) and the
  backend discovers/pulls their leagues directly — fully public API, no
  auth artifacts to manage.
- **Yahoo**: standard OAuth 2.0 authorization-code flow, done once through a
  browser redirect into the backend; backend stores the refresh token and
  polls independently from then on.
- **ESPN**: user manually copies `espn_s2` and `SWID` cookie values from
  their browser's dev tools (this is the standard method community ESPN API
  libraries already document) and pastes them into a LeagueDeck settings
  field once. These cookies are long-lived (weeks to months) but will
  eventually expire and need re-pasting — there is no way around this
  without either storing an ESPN password or building browser automation,
  neither of which is justified for Phase 1 read access.

### Sync

An APScheduler job polls each connected platform on an interval (e.g. every
15–30 minutes, tunable) and writes normalized results into SQLite. The
dashboard always reads from SQLite, not live from the platforms, so it loads
fast and doesn't hammer any platform's API on every page view.

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

## Dashboard features (Phase 1, in build order)

1. **Unified roster + matchup view** — every team the user owns, across
   every connected league/platform, in one screen: current
   starters/bench, this week's opponent and score. This is the foundation
   every other feature is built on top of.
2. **Injury/news alerts** — a single feed surfacing any rostered player
   across any team who is questionable/out/on a bye this week.
3. **Start/sit recommendations** — adapts the VORP/projections scoring
   logic from `fantasy-auction-assistant/backend/engine.py` (originally
   built for auction values) into a season-long lineup-optimization score,
   suggesting the best starting lineup per team based on projections.
4. **Waiver wire / trending pickups** — combines Sleeper's trending-adds
   endpoint with each team's open roster spots and positional needs to
   surface add/drop suggestions.

## Phase 2 (sketched, not built now): one-click lineup writes

```
┌─────────────▼─────────────────────────────────────┐
│         Playwright write-worker (separate process)  │
│  headless browser + stored ESPN/Sleeper cookies      │
│  navigates to the real lineup page, performs the      │
│  actual UI actions to set your lineup                │
└─────────────────────────────────────────────────────┘
```

- **Yahoo**: add a `PUT` call to `yahoo.py` against the official roster
  endpoint, using the OAuth token already obtained in Phase 1. Low risk,
  officially supported.
- **ESPN / Sleeper**: since there's no extension and no write API, the
  backend hands a "set this lineup" job to a **Playwright headless-browser
  worker** — a separate process that loads the stored cookies into a real
  browser context, navigates to the actual lineup-edit page, and scripts
  the same clicks/drags a human would to submit the change.

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
via Tailscale or LAN. `docker-compose up` is the entire install step. No
cloud hosting, no ongoing hosting cost, no secrets leaving the user's own
hardware.

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
- Specific polling intervals per platform (balance freshness vs. rate
  limits — Sleeper docs suggest staying well under 1000 req/min; ESPN and
  Yahoo have no published personal-use limits but should be polled
  conservatively).
- Whether the start/sit engine reuses `engine.py` via a shared package or is
  ported/rewritten standalone (auction-day scoring assumptions differ from
  season-long lineup scoring).
