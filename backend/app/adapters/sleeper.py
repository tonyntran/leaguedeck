import json
import time
from pathlib import Path

import httpx

SLEEPER_BASE_URL = "https://api.sleeper.app/v1"
PLAYERS_CACHE_PATH = Path("data/sleeper_players_cache.json")
PLAYERS_CACHE_MAX_AGE_SECONDS = 24 * 60 * 60  # Sleeper asks this endpoint not be hit often


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
            }
            for pid in (roster.get("players") or [])
        ]

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

    return {
        "platform": "sleeper",
        "platform_league_id": league_id,
        "name": league_data["name"],
        "season": league_data["season"],
        "teams": teams,
    }
