import json
import logging
import time
from pathlib import Path

import httpx

from app.schedule import is_likely_live_window

logger = logging.getLogger(__name__)

SLEEPER_BASE_URL = "https://api.sleeper.app/v1"
PLAYERS_CACHE_PATH = Path("data/sleeper_players_cache.json")
PLAYERS_CACHE_MAX_AGE_SECONDS = 24 * 60 * 60  # Sleeper asks this endpoint not be hit often

# Actual stats and projections live on an entirely different, undocumented
# host than the rest of this adapter's documented api.sleeper.app/v1 base --
# a real fragility step up, accepted knowingly since neither has an official
# alternative.
SLEEPER_STATS_HOST = "https://api.sleeper.com"
# "stats" (actual, already-played performance) changes during live games, so
# outside a live window its TTL matches the 20-minute baseline sync, but
# during a live window it matches the fast 60s sync cadence instead (see
# main.py/schedule.py) -- otherwise a card's per-player actuals would sit
# frozen in cache for up to 20 minutes while the team total above them
# (which isn't cached) ticks up every 60 seconds, defeating the point of
# the faster cadence. Projections barely move in-week regardless of live
# windows, so they keep the long TTL to spare the undocumented host traffic.
CACHE_MAX_AGE_SECONDS = {"stats": 20 * 60, "projections": 60 * 60}
LIVE_WINDOW_STATS_CACHE_MAX_AGE_SECONDS = 60


class SleeperAdapterError(Exception):
    pass


def get_user_id(username: str) -> str:
    resp = httpx.get(f"{SLEEPER_BASE_URL}/user/{username}", timeout=10.0)
    if resp.status_code != 200:
        raise SleeperAdapterError(f"Sleeper user lookup failed: {resp.status_code}")
    data = resp.json()
    if not data:
        raise SleeperAdapterError(f"Sleeper user '{username}' not found")
    return data["user_id"]


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


def get_players_map() -> dict:
    """Returns {player_id: {"full_name", "position", "team"}}, cached on disk
    for up to 24h since Sleeper's docs ask this bulk endpoint not be polled often."""
    if PLAYERS_CACHE_PATH.exists():
        age = time.time() - PLAYERS_CACHE_PATH.stat().st_mtime
        if age < PLAYERS_CACHE_MAX_AGE_SECONDS:
            return json.loads(PLAYERS_CACHE_PATH.read_text())

    resp = httpx.get(f"{SLEEPER_BASE_URL}/players/nfl", timeout=30.0)
    resp.raise_for_status()
    raw = resp.json()
    players_map = {
        pid: {
            "full_name": p.get("full_name") or p.get("last_name") or pid,
            "position": p.get("position"),
            "team": p.get("team"),
        }
        for pid, p in raw.items()
    }
    PLAYERS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAYERS_CACHE_PATH.write_text(json.dumps(players_map))
    return players_map


def _fetch_weekly_totals(kind: str, season: str, week: int) -> dict:
    """kind is "stats" (actual, already-played performance) or "projections"
    (pre-game estimate). Both live on SLEEPER_STATS_HOST and share the same
    list-of-entries shape: each entry has a player_id and a `stats` sub-dict
    with pre-computed pts_ppr/pts_half_ppr/pts_std totals -- verified live
    against the real (undocumented) endpoints during development."""
    max_age = CACHE_MAX_AGE_SECONDS[kind]
    if kind == "stats" and is_likely_live_window():
        max_age = LIVE_WINDOW_STATS_CACHE_MAX_AGE_SECONDS

    cache_path = Path(f"data/sleeper_{kind}_cache_{season}_{week}.json")
    if cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < max_age:
            return json.loads(cache_path.read_text())

    resp = httpx.get(
        f"{SLEEPER_STATS_HOST}/{kind}/nfl/{season}/{week}?season_type=regular",
        timeout=15.0,
    )
    resp.raise_for_status()
    raw = resp.json()
    totals = {
        entry["player_id"]: {
            "pts_ppr": (entry.get("stats") or {}).get("pts_ppr"),
            "pts_half_ppr": (entry.get("stats") or {}).get("pts_half_ppr"),
            "pts_std": (entry.get("stats") or {}).get("pts_std"),
        }
        for entry in raw
        if entry.get("player_id")
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(totals))
    return totals


def get_actual_stats(season: str, week: int) -> dict:
    """Returns {player_id: {"pts_ppr", "pts_half_ppr", "pts_std"}} for
    already-played performance in the given week."""
    return _fetch_weekly_totals("stats", season, week)


def get_projections(season: str, week: int) -> dict:
    """Returns {player_id: {"pts_ppr", "pts_half_ppr", "pts_std"}} for
    pre-game estimates in the given week."""
    return _fetch_weekly_totals("projections", season, week)


def _pick_points(totals_entry: dict | None, scoring_settings: dict) -> float | None:
    """Sleeper's stats/projections endpoints return three pre-computed point
    totals (PPR/half-PPR/standard) rather than raw stat categories, so
    instead of reimplementing Sleeper's full scoring formula this picks
    whichever matches the league's own reception-point value -- exact for
    standard PPR/half-PPR/standard leagues, approximate for anyone with more
    heavily customized scoring (bonus points, TE premium, etc.) -- an
    accepted, named cost like the other cross-platform approximations
    already in this app."""
    if not totals_entry:
        return None
    # `.get("rec", 0)` alone would not catch an explicit `"rec": null` --
    # Sleeper is unlikely to send that, but the `or 0` costs nothing and
    # avoids a `None >= 1` TypeError taking down the whole roster loop.
    rec_points = scoring_settings.get("rec") or 0
    if rec_points >= 1:
        return totals_entry.get("pts_ppr")
    if rec_points >= 0.5:
        return totals_entry.get("pts_half_ppr")
    return totals_entry.get("pts_std")


def _get_league(league_id: str) -> dict:
    resp = httpx.get(f"{SLEEPER_BASE_URL}/league/{league_id}", timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def _get_rosters(league_id: str) -> list[dict]:
    resp = httpx.get(f"{SLEEPER_BASE_URL}/league/{league_id}/rosters", timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def _get_league_users(league_id: str) -> list[dict]:
    resp = httpx.get(f"{SLEEPER_BASE_URL}/league/{league_id}/users", timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def _get_matchups(league_id: str, week: int) -> list[dict]:
    resp = httpx.get(f"{SLEEPER_BASE_URL}/league/{league_id}/matchups/{week}", timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def get_weekly_points(league_id: str, season: str, week: int) -> tuple[dict, dict]:
    """Returns (actual_points, projected_points), each {player_id: float |
    None}, already resolved to this league's own scoring format. Used by the
    waiver-wire endpoint, which (unlike normalize_league) doesn't otherwise
    fetch this league's data or scoring settings."""
    scoring_settings = _get_league(league_id).get("scoring_settings") or {}
    actual = get_actual_stats(season, week)
    projected = get_projections(season, week)
    return (
        {pid: _pick_points(totals, scoring_settings) for pid, totals in actual.items()},
        {pid: _pick_points(totals, scoring_settings) for pid, totals in projected.items()},
    )


def _team_name_of(owner: dict) -> str | None:
    """Preferred display name for a league member: their custom team name if set,
    else their Sleeper display name. Sleeper sends `metadata: null` (not an absent
    key) for members who never customized, so the null must be coalesced here."""
    metadata = owner.get("metadata") or {}
    return metadata.get("team_name") or owner.get("display_name")


def normalize_league(league_id: str, my_user_id: str, players_map: dict) -> dict:
    """Fetch everything for one Sleeper league and normalize into LeagueDeck's shape."""
    league_data = _get_league(league_id)
    rosters = _get_rosters(league_id)
    users = _get_league_users(league_id)
    week = league_data["settings"]["leg"]  # Sleeper's name for "current week"
    matchups = _get_matchups(league_id, week)
    scoring_settings = league_data.get("scoring_settings") or {}

    # A hiccup on the undocumented stats/projections host is a "nice to
    # have" feature failing, not a reason to fail the whole league sync --
    # degrade to no actual/projected points rather than losing roster,
    # score, and opponent data over it.
    try:
        actual_stats = get_actual_stats(league_data["season"], week)
    except Exception:
        logger.exception("sleeper: actual stats lookup failed for league %s week %s", league_id, week)
        actual_stats = {}
    try:
        projections = get_projections(league_data["season"], week)
    except Exception:
        logger.exception("sleeper: projections lookup failed for league %s week %s", league_id, week)
        projections = {}

    users_by_id = {u["user_id"]: u for u in users}
    matchups_by_roster_id = {m["roster_id"]: m for m in matchups}
    # Sleeper returns a row for every roster every week, using matchup_id: null for
    # rosters with no game that week (playoff byes, consolation gaps, odd team counts).
    # Those must not be indexed, or they would all collapse under a single None key and
    # be read back as one shared matchup -- pairing bye rosters into games nobody played.
    matchups_by_matchup_id: dict[int, list[dict]] = {}
    for m in matchups:
        if m.get("matchup_id") is None:
            continue
        matchups_by_matchup_id.setdefault(m["matchup_id"], []).append(m)

    teams = []
    total_roster_players = 0
    resolved_roster_players = 0
    for roster in rosters:
        owner = users_by_id.get(roster["owner_id"], {})
        team_name = (
            _team_name_of(owner) or f"Roster {roster['roster_id']}"
        )

        my_matchup = matchups_by_roster_id.get(roster["roster_id"])
        opponent_name = None
        opponent_points = None
        my_points = my_matchup["points"] if my_matchup else 0.0

        # A null matchup_id means "no game this week" -- same result as having no row
        # at all: own points are still reported, but there is no opponent.
        if my_matchup and my_matchup.get("matchup_id") is not None:
            group = matchups_by_matchup_id.get(my_matchup["matchup_id"], [])
            opponent = next((m for m in group if m["roster_id"] != roster["roster_id"]), None)
            if opponent:
                opp_roster = next(
                    (r for r in rosters if r["roster_id"] == opponent["roster_id"]), None
                )
                if opp_roster:
                    opp_owner = users_by_id.get(opp_roster["owner_id"], {})
                    opponent_name = _team_name_of(opp_owner)
                opponent_points = opponent["points"]

        starter_ids = set(roster.get("starters") or [])
        roster_players = [
            {
                "player_id": pid,
                "name": players_map.get(pid, {}).get("full_name", pid),
                "position": players_map.get(pid, {}).get("position"),
                "team": players_map.get(pid, {}).get("team"),
                "is_starter": pid in starter_ids,
                "actual_points": _pick_points(actual_stats.get(pid), scoring_settings),
                "projected_points": _pick_points(projections.get(pid), scoring_settings),
            }
            for pid in (roster.get("players") or [])
        ]
        total_roster_players += len(roster_players)
        resolved_roster_players += sum(
            1
            for p in roster_players
            if p["actual_points"] is not None or p["projected_points"] is not None
        )

        teams.append(
            {
                "platform_team_id": str(roster["roster_id"]),
                "name": team_name,
                "is_mine": roster["owner_id"] == my_user_id,
                "roster_json": roster_players,
                "points_for": my_points,
                "opponent_name": opponent_name,
                "opponent_points": opponent_points,
                "week": week,
            }
        )

    # The stats/projections fetch can succeed (no exception, so the guards
    # above stay silent) while still matching zero players -- e.g. Sleeper
    # renaming pts_ppr, or an off-by-one on the computed week. That failure
    # mode would otherwise be indistinguishable from "no games have started
    # yet" (also all-None). This is the one signal that tells them apart.
    if total_roster_players and not resolved_roster_players and (actual_stats or projections):
        logger.warning(
            "sleeper: fetched %d actual + %d projected entries but resolved 0/%d roster "
            "players' points for league %s week %s -- possible schema drift",
            len(actual_stats),
            len(projections),
            total_roster_players,
            league_id,
            week,
        )

    return {
        "platform": "sleeper",
        "platform_league_id": league_id,
        "name": league_data["name"],
        "season": league_data["season"],
        "teams": teams,
    }
