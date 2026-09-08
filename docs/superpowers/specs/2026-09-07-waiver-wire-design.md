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
  limit=25) -> list[dict]` calling Sleeper's trending endpoint, returning
  `[{"player_id": str, "count": int}, ...]` exactly as Sleeper returns it (no
  transformation needed at this layer). Build the URL as an f-string with
  the query params embedded directly —
  `f"{SLEEPER_BASE_URL}/players/nfl/trending/add?lookback_hours={lookback_hours}&limit={limit}"`
  — matching every other function in this file (none of them use httpx's
  `params=` kwarg). This is a deliberate consistency choice, not an
  oversight: the existing adapter tests fake `httpx.get` keyed by exact URL
  string (`fake_get(url, timeout=10.0)`), and embedding params in the URL
  means this function's tests need zero changes to that shared test
  infrastructure. Using `params=` instead would require widening every
  existing fake in the test suite for one new function.
- **`app/routers/leagues.py`**: new endpoint `GET
  /leagues/{league_id}/waiver-wire`, auth-protected like every other route
  in this router.
  - 404 if no `League` row matches `league_id`, and 404 if
    `league.platform != "sleeper"` (explicit, not "not this platform's
    league" — ESPN/Yahoo leagues will exist as rows once those plans land,
    and this endpoint has no Sleeper-agnostic path yet).
  - Query all `Team` rows for that league and union every `player_id` out
    of their `roster_json` into an exclusion set. If the league has zero
    `Team` rows (sync hasn't populated it yet, or failed), return an empty
    list rather than the raw unfiltered trending set — showing "trending,
    unfiltered" would misrepresent already-rostered players as available.
    Note this exclusion set is only as fresh as the league's last
    successful sync, the same staleness bound every other synced field on
    this dashboard already has — not a new problem this feature
    introduces.
  - Call `sleeper.get_trending_adds()`. **If this call raises (Sleeper
    down, network error, rate limit), catch it and return an empty list**
    rather than letting the exception propagate into a 500 — this is a
    nice-to-have feature, and a Sleeper hiccup on the trending endpoint
    should not read as "your dashboard is broken." The frontend's existing
    empty-state rendering (see below) covers this without extra backend
    signaling.
  - Resolve each trending player's name/position/team via
    `sleeper.get_players_map()`, using the exact same fallback convention
    `normalize_league` already established for unknown IDs:
    `players_map.get(pid, {}).get("full_name", pid)` for name, `None` for
    position/team when the ID isn't in the map. Don't invent a different
    convention for this endpoint.
  - On `get_players_map()`'s cold-cache cost (a 30s bulk download when the
    24h disk cache has expired): this is an accepted, deliberate tradeoff,
    not an oversight. By the time a user is viewing a populated dashboard,
    at least one sync has already run (startup or the 20-minute schedule),
    and that sync already calls `get_players_map()` — so in practice the
    cache is almost always warm by the time waiver-wire is requested. The
    remaining edge case (multiple leagues' waiver-wire requests firing in
    parallel from the frontend, all hitting a cold cache at once) has no
    lock today; each concurrent caller would redundantly re-download and
    overwrite the same cache file. This is wasteful but not corrupting —
    `write_text` writes a complete, valid JSON dump each time, so the
    worst case is last-write-wins with a few redundant downloads, not a
    torn file. Not worth a lock for personal-use scale; revisit only if it
    becomes a real problem.
  - Return the filtered, resolved list sorted by trend count descending:
    `[{"player_id", "name", "position", "team", "trend_count"}]`.
- No new database tables or columns — this reads existing synced data plus
  one live Sleeper call.
- No nginx/Vite proxy changes needed. Traced directly: the existing pattern
  `^(/auth|/leagues|/sync-status|/health)(/|$)|^/settings/.+` (no
  end-anchor) matches `/leagues/12/waiver-wire` at the `(/leagues)(/|$)`
  branch — `/leagues` followed by a literal `/` satisfies that branch
  regardless of what follows, the same way `/sync-status/run` already
  proxies correctly without a dedicated rule.

## Frontend changes

- **`api/client.js`**: new `getWaiverWire(leagueId)` calling the new
  endpoint.
- **`pages/DashboardPage.jsx`**: a "Waiver Wire" section added to each
  league's team card, below Bench, reusing the `.ld-lineup-section`
  block-level pattern already established for Starting Lineup/Bench.
  - **Load sequencing, corrected from an earlier draft of this spec**:
    `getWaiverWire(leagueId)` cannot load "alongside" `getLeagues()` —
    league IDs only exist in `getLeagues()`'s response. It must chain
    after `getLeagues()` resolves: once the league list is in, fire one
    `getWaiverWire(league.id)` call per league (`Promise.all` or
    equivalent) rather than sequentially.
  - Track waiver-wire state per league (e.g. a `{[leagueId]: players[] |
    null}` map), not a single shared list — each league's fetch succeeds
    or fails independently and shouldn't block the others.
  - On a per-league fetch failure, swallow it and render an empty/absent
    Waiver Wire section for that league only (same pattern already used
    for `getSyncStatus()`'s `.catch(() => {})` in `loadData()`) — one
    league's trending-fetch failure must not break the rest of the
    dashboard.
  - **New row markup, not a reuse of `.ld-roster-row`**: the existing
    roster row is a 3-slot layout (`.ld-pos`, `.ld-nm`, `.ld-tm`) where
    `.ld-tm { margin-left: auto }` assumes the team abbreviation is the
    last element. A waiver row needs a 4th value (trend count), so this
    needs its own row class (e.g. `.ld-waiver-row`) rather than forcing a
    4th column into the existing 3-slot flex layout. The section-level
    container (`.ld-lineup-section`, `.ld-lineup-label`) is genuinely
    reusable as-is; the row markup is not.

## Testing

- Adapter test for `get_trending_adds()` mocking `httpx.get`, matching the
  existing Sleeper adapter test pattern (fake responses keyed by exact URL
  string, including the embedded query params).
- Router tests seeding a league with known rosters across multiple teams,
  monkeypatching `get_trending_adds()`/`get_players_map()`:
  - Already-rostered trending players are excluded while free ones are
    returned, sorted by trend count descending.
  - A league with zero `Team` rows returns an empty list, not the raw
    trending set.
  - A trending player ID absent from `players_map` falls back to the raw
    ID for name and `None` for position/team (matching
    `normalize_league`'s convention).
  - `get_trending_adds()` raising is caught and the endpoint still returns
    200 with an empty list, not a 500.
  - Non-Sleeper `League.platform` (or a nonexistent `league_id`) returns
    404.
- No frontend automated tests, consistent with the rest of the project.

## Non-goals (explicitly out of scope, per the scope decision above)

- Positional-need matching.
- Drop suggestions.
- Caching/rate-limiting the trending call beyond what's described above —
  revisit only if it becomes a real problem.
