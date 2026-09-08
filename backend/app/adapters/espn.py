import httpx

ESPN_BASE_URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons"

BENCH_SLOT_ID = 20
IR_SLOT_ID = 21

# defaultPositionId -> the string vocabulary the rest of this app already
# uses (Sleeper normalizes to the same strings). ESPN's own label for 16 is
# "D/ST" -- it must map to "DEF" here, not carried through verbatim, or the
# frontend's POSITION_ORDER array (Sleeper's vocabulary) won't recognize it.
POSITION_MAP = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF"}

# proTeamId -> team abbreviation, matching the 2-3 letter convention Sleeper's
# `team` field already uses. IDs are not contiguous (31/32 unused; 33/34 are
# later-added expansion teams) -- this is ESPN's actual numbering, not a typo.
# 0 (free agent / no team) maps to None, matching Sleeper's null convention.
PRO_TEAM_MAP = {
    0: None,
    1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN",
    8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR",
    15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ",
    21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA",
    27: "TB", 28: "WAS", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}


class EspnAdapterError(Exception):
    pass


def _normalize_guid(raw: str) -> str:
    """ESPN GUIDs are brace-wrapped and case can differ between what a user
    pastes from DevTools and what the API echoes back in `owners` -- strip
    braces and uppercase before comparing so a cosmetic mismatch doesn't
    misidentify (or fail to identify) the user's own team."""
    return raw.strip().strip("{}").upper()


def _canonical_swid_cookie(swid: str) -> str:
    """ESPN's API expects the SWID cookie in canonical brace-wrapped,
    uppercase form -- a user who pastes it without braces or in lowercase
    (nothing in the UI currently enforces the exact format) would otherwise
    send a cookie ESPN rejects, even though our own is_mine comparison
    (_normalize_guid) already tolerates the same variation."""
    return "{" + _normalize_guid(swid) + "}"


def _get_combined_view(league_id: str, season: int, espn_s2: str, swid: str) -> dict:
    url = f"{ESPN_BASE_URL}/{season}/segments/0/leagues/{league_id}"
    resp = httpx.get(
        url,
        params=[
            ("view", "mTeam"),
            ("view", "mRoster"),
            ("view", "mMatchup"),
            ("view", "mSettings"),
        ],
        cookies={"espn_s2": espn_s2, "SWID": _canonical_swid_cookie(swid)},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


def _team_display_name(team: dict) -> str:
    name = (team.get("name") or "").strip()
    if name:
        return name
    return f"{team.get('location', '')} {team.get('nickname', '')}".strip()


def _team_score(schedule: list[dict], week: int, team_id: int):
    """Returns (points_for, opponent_team_id, opponent_points) for one team in
    one week, or (0.0, None, None) if the team has no scheduled game that week
    (bye week in an odd-team league) -- matching Sleeper's convention of
    reporting 0.0 rather than None for a team's own missing score."""
    for game in schedule:
        if game.get("matchupPeriodId") != week:
            continue
        home, away = game.get("home") or {}, game.get("away") or {}
        if home.get("teamId") == team_id:
            return home.get("totalPoints", 0.0), away.get("teamId"), away.get("totalPoints")
        if away.get("teamId") == team_id:
            return away.get("totalPoints", 0.0), home.get("teamId"), home.get("totalPoints")
    return 0.0, None, None


def normalize_league(league_id: str, season: int, espn_s2: str, swid: str) -> dict:
    """Fetch everything for one ESPN league and normalize into LeagueDeck's
    shared League/Team shape (the same shape sleeper.normalize_league produces)."""
    data = _get_combined_view(league_id, season, espn_s2, swid)

    # Assumes scoringPeriodId (ESPN's "current NFL week") equals matchupPeriodId
    # for the purposes of finding this week's game in `schedule` -- true for
    # standard weekly matchups, but unverified against a real league for
    # multi-week playoff matchups, where the two can diverge. If they diverge,
    # _team_score's "no game found" fallback (0.0, None, None) applies silently
    # rather than erroring -- flag this specifically when real-league
    # verification is eventually run.
    week = data["scoringPeriodId"]
    schedule = data.get("schedule") or []
    my_guid = _normalize_guid(swid)

    teams_by_id = {t["id"]: t for t in data.get("teams") or []}

    teams = []
    for team in teams_by_id.values():
        points_for, opponent_team_id, opponent_points = _team_score(schedule, week, team["id"])
        opponent = teams_by_id.get(opponent_team_id)
        opponent_name = _team_display_name(opponent) if opponent else None

        owners = team.get("owners") or []
        is_mine = any(_normalize_guid(o) == my_guid for o in owners)

        entries = ((team.get("roster") or {}).get("entries")) or []
        roster_players = []
        for entry in entries:
            player = (entry.get("playerPoolEntry") or {}).get("player") or {}
            slot_id = entry.get("lineupSlotId")
            player_id = str(player.get("id"))
            roster_players.append(
                {
                    "player_id": player_id,
                    "name": player.get("fullName") or player_id,
                    "position": POSITION_MAP.get(player.get("defaultPositionId")),
                    "team": PRO_TEAM_MAP.get(player.get("proTeamId")),
                    "is_starter": slot_id not in (BENCH_SLOT_ID, IR_SLOT_ID),
                }
            )

        teams.append(
            {
                "platform_team_id": str(team["id"]),
                "name": _team_display_name(team),
                "is_mine": is_mine,
                "roster_json": roster_players,
                "points_for": points_for,
                "opponent_name": opponent_name,
                "opponent_points": opponent_points,
                "week": week,
            }
        )

    return {
        "platform": "espn",
        "platform_league_id": league_id,
        "name": (data.get("settings") or {}).get("name") or f"ESPN League {league_id}",
        "season": str(season),
        "teams": teams,
    }
