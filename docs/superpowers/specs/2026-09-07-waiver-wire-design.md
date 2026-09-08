# Waiver Wire — Design Spec

Date: 2026-09-07
Status: Approved, pre-implementation

## Problem

The original design spec (`2026-09-07-leaguedeck-design.md`) scoped "waiver
wire / trending pickups" as dashboard feature 4, deferred out of the
foundation-and-Sleeper plan. With the foundation now live and the dashboard
redesigned around "your team, organized" (per the mid-implementation pivot
away from showing every team's roster), this is the next feature: surface
players worth picking up that aren't yet on your radar.

## Scope, as decided with the user

- **Trending + not-already-rostered only.** No positional-need matching
  (e.g. "only show RBs because you're thin at RB") — that requires modeling
  roster slot requirements, which nothing in the codebase does yet. A
  simpler, still-useful v1: platform-wide trending adds, filtered to players
  nobody in the relevant league has rostered.
- **Adds only, no drop suggestions.** Suggesting what to drop needs some
  notion of "weak player," which needs real projections data the project
  doesn't have. Explicitly out of scope for this round.

## Constraint from Sleeper's API

Sleeper's trending endpoint (`GET /v1/players/nfl/trending/add`) is
platform-wide — "most added across all of Sleeper in the last N hours," not
specific to any one league. All of the actual personalization happens on
our side: cross-referencing trending player IDs against the specific
league's already-rostered players (which the sync job already stores in
`Team.roster_json`).

Because "already rostered" only makes sense per-league (a player could be
free in one of the user's leagues and owned in another), this is computed
per-league, not globally.

## Architecture

```
Frontend                Backend                              Sleeper API
   │                        │                                     │
   │ GET /leagues/{id}/     │                                     │
   │   waiver-wire          │                                     │
   ├───────────────────────►│                                     │
   │                        │  get_trending_adds() ──────────────►│
   │                        │◄──────────────────── [{player_id,   │
   │                        │                        count}, ...] │
   │                        │                                     │
   │                        │  query Team.roster_json for every   │
   │                        │  team in this league (already in    │
   │                        │  our own DB, no extra Sleeper call)  │
   │                        │                                     │
   │                        │  filter out already-rostered IDs,   │
   │                        │  resolve names via get_players_map()│
   │◄───────────────────────┤  (existing 24h-cached bulk lookup)  │
   │  [{player_id, name,    │                                     │
   │    position, team,     │                                     │
   │    trend_count}, ...]  │                                     │
```

**Computed live per request, not synced on a schedule.** "Trending" is
inherently a live concept — storing it would just mean showing stale
trends. The trending endpoint is a light, targeted call, unlike the bulk
players dump Sleeper's docs specifically ask not to be polled often; calling
it on page load is not the same class of concern.

## Backend changes

- **`app/adapters/sleeper.py`**: new `get_trending_adds(lookback_hours=24,
  limit=25) -> list[dict]` calling `GET /v1/players/nfl/trending/add` with
  those query params, returning `[{"player_id": str, "count": int}, ...]`
  exactly as Sleeper returns it (no transformation needed at this layer).
- **`app/routers/leagues.py`**: new endpoint `GET
  /leagues/{league_id}/waiver-wire`, auth-protected like every other route
  in this router. Looks up the `League` row by id (404 if not found or not
  this platform's league), queries all `Team` rows for that league, unions
  every `player_id` out of their `roster_json`, calls
  `sleeper.get_trending_adds()` and `sleeper.get_players_map()`, filters out
  already-rostered IDs, and returns the remainder sorted by trend count
  descending: `[{"player_id", "name", "position", "team", "trend_count"}]`.
- No new database tables or columns — this reads existing synced data plus
  one live Sleeper call.
- No nginx/Vite proxy changes needed: `GET /leagues/{id}/waiver-wire`
  already matches the existing proxy pattern's `^(/leagues)(/|$)` branch
  (the same way `/sync-status/run` was already covered without touching the
  proxy config) — confirmed by the same reasoning verified during the final
  whole-branch review of the foundation plan.

## Frontend changes

- **`api/client.js`**: new `getWaiverWire(leagueId)` calling the new
  endpoint.
- **`pages/DashboardPage.jsx`**: a "Waiver Wire" section added to each
  league's team card, below Bench, using the same Trading Card Wall visual
  language already established (`.ld-lineup-section`-style block). A simple
  ranked list: name, position/team, trend count. Loaded alongside the
  existing `getLeagues()`/`getSyncStatus()` calls in `loadData()`.

## Testing

- Adapter test for `get_trending_adds()` mocking `httpx.get`, matching the
  existing Sleeper adapter test pattern (fake responses keyed by URL).
- Router test seeding a league with known rosters across multiple teams,
  monkeypatching `get_trending_adds()`/`get_players_map()`, and asserting
  already-rostered trending players are excluded while free ones are
  returned with the right sort order.
- No frontend automated tests, consistent with the rest of the project.

## Non-goals (explicitly out of scope, per the scope decision above)

- Positional-need matching.
- Drop suggestions.
- Caching/rate-limiting the trending call beyond what's described above —
  revisit only if it becomes a real problem.
