import base64
import logging

import httpx

logger = logging.getLogger(__name__)

YAHOO_OAUTH_HOST = "https://api.login.yahoo.com/oauth2"
YAHOO_FANTASY_BASE_URL = "https://fantasysports.yahooapis.com/fantasy/v2"
OOB_REDIRECT_URI = "oob"


class YahooAdapterError(Exception):
    pass


def _reformat(obj) -> dict:
    """Yahoo's ?format=json output is a mechanical XML->JSON conversion
    with two different list-like shapes depending on context: mixed
    sibling elements at the same XML level (e.g. <league> next to <time>
    next to <copyright>) render as a genuine JSON array of single-key
    dicts, while repeated identical sibling elements (e.g. several <team>
    elements) render as an indexed object {"0": item0, "1": item1, ...,
    "count": N}. This merges either shape into one flat dict so a single
    .get(key) works regardless of which shape a particular response used
    at a particular nesting level. Verified against the actual navigation
    logic (query()/reformat_json_list) in yfpy, the leading Python wrapper
    for this API -- not yet confirmed against a live response from this
    app. See the design spec's Testing section: this is the first thing to
    verify once real Yahoo API access is available."""
    if isinstance(obj, list):
        merged: dict = {}
        for item in obj:
            if isinstance(item, dict):
                merged.update(item)
        return merged
    if isinstance(obj, dict):
        return obj
    return {}


def _unwrap_list(obj) -> list:
    """Given a value expected to be a repeated collection (a league's
    teams, a team's roster entries, a matchup's two teams), returns a
    plain Python list -- dropping the "count" sentinel from Yahoo's
    indexed-object shape, or passing a genuine JSON list through
    unchanged. A single wrapped value (no "count" sibling, since XML has
    no distinct "definitely one item" representation) also unwraps
    correctly; callers take element [0] at the call site if they know
    only one value is expected."""
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        return [v for k, v in obj.items() if k != "count"]
    return []


def _navigate(fantasy_content, *keys: str):
    """Walks fantasy_content by each key in turn, reformatting at each
    step so mixed-shape nesting doesn't need a special case per level.
    Mirrors yfpy's query() drilling logic, confirmed against its source."""
    current = fantasy_content
    for key in keys:
        current = _reformat(current).get(key)
    return current


def _find_collection(container, key: str):
    """A repeated collection (players, matchups, teams) can sit directly as
    a sibling key on its parent, or nested one level inside an indexed
    wrapper alongside other scalar siblings (e.g. roster's coverage_type
    appearing before players) -- try the direct read first, and only search
    inside _unwrap_list(container) if that comes up empty."""
    direct = _reformat(container).get(key)
    if direct is not None:
        return direct
    for entry in _unwrap_list(container):
        candidate = _reformat(entry).get(key)
        if candidate is not None:
            return candidate
    return None


def _basic_auth_header(client_id: str, client_secret: str) -> dict:
    token = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def get_authorize_url(client_id: str) -> str:
    """Builds the URL the user opens in a new tab to approve access. Yahoo
    shows a one-time verification code on-screen (the "oob" -- out of
    band -- flow) rather than redirecting anywhere, since this app has no
    HTTPS listener to receive a redirect."""
    return (
        f"{YAHOO_OAUTH_HOST}/request_auth"
        f"?client_id={client_id}&redirect_uri={OOB_REDIRECT_URI}&response_type=code"
    )


def _parse_token_response(resp: httpx.Response) -> dict:
    resp.raise_for_status()
    data = resp.json()
    return {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_in": data["expires_in"],
        "yahoo_guid": data.get("xoauth_yahoo_guid"),
    }


def exchange_code_for_tokens(client_id: str, client_secret: str, code: str) -> dict:
    """Exchanges a one-time verification code (pasted by the user from
    Yahoo's oob approval page) for an access/refresh token pair. Never log
    the code, this function's return value, or the raw response body --
    all are live credentials."""
    resp = httpx.post(
        f"{YAHOO_OAUTH_HOST}/get_token",
        headers=_basic_auth_header(client_id, client_secret),
        data={
            "redirect_uri": OOB_REDIRECT_URI,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=10.0,
    )
    return _parse_token_response(resp)


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    """Same endpoint as exchange_code_for_tokens, but trading a refresh
    token for a new access token instead of a one-time code. redirect_uri
    is required on this call too, even though no redirect happens -- omit
    it and the resulting error looks identical to an expired refresh
    token."""
    resp = httpx.post(
        f"{YAHOO_OAUTH_HOST}/get_token",
        headers=_basic_auth_header(client_id, client_secret),
        data={
            "redirect_uri": OOB_REDIRECT_URI,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=10.0,
    )
    return _parse_token_response(resp)


BENCH_SLOT = "BN"
IR_SLOTS = ("IR", "IR+", "IR-R")


def _get(url: str, access_token: str) -> dict:
    resp = httpx.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()


def _league_key(league_id: str) -> str:
    return f"nfl.l.{league_id}"


def _get_league_metadata(league_key: str, access_token: str) -> dict:
    data = _get(f"{YAHOO_FANTASY_BASE_URL}/league/{league_key}/metadata", access_token)
    league_data = _navigate(data.get("fantasy_content"), "league")
    return _reformat(league_data)


def _manager_guids(team_entries: list) -> list[str]:
    managers_raw = _reformat(team_entries).get("managers")
    guids = []
    for entry in _unwrap_list(managers_raw):
        manager = _reformat(entry).get("manager") or {}
        guid = manager.get("guid")
        if guid:
            guids.append(guid)
    return guids


def _get_league_teams(league_key: str, access_token: str) -> list[dict]:
    """Returns one dict per team: {"team_key", "name", "manager_guids"}."""
    data = _get(f"{YAHOO_FANTASY_BASE_URL}/league/{league_key}/teams", access_token)
    league_data = _navigate(data.get("fantasy_content"), "league")
    teams_raw = _reformat(league_data).get("teams")
    teams = []
    for entry in _unwrap_list(teams_raw):
        team_entries = _reformat(entry).get("team")
        flat = _reformat(team_entries)
        teams.append(
            {
                "team_key": flat.get("team_key"),
                "name": flat.get("name"),
                "manager_guids": _manager_guids(team_entries),
            }
        )
    return teams


def _get_team_roster(team_key: str, week: int | None, access_token: str) -> list[dict]:
    # When the current week couldn't be resolved (e.g. scoreboard schema
    # drift), omit the ;week= segment entirely rather than sending a
    # literal "week=None" that Yahoo would reject -- Yahoo defaults to the
    # current week when the parameter is absent.
    week_segment = f";week={week}" if week is not None else ""
    data = _get(f"{YAHOO_FANTASY_BASE_URL}/team/{team_key}/roster{week_segment}", access_token)
    team_data = _navigate(data.get("fantasy_content"), "team")
    roster_raw = _reformat(team_data).get("roster")
    players_raw = _find_collection(roster_raw, "players")

    roster_players = []
    for entry in _unwrap_list(players_raw):
        player_entries = _reformat(entry).get("player")
        flat = _reformat(player_entries)
        name_field = _reformat(flat.get("name")) or {}
        slot_entries = flat.get("selected_position")
        slot = _reformat(slot_entries).get("position")
        player_id = str(flat.get("player_id"))
        roster_players.append(
            {
                "player_id": player_id,
                "name": name_field.get("full") or player_id,
                "position": flat.get("display_position"),
                "team": flat.get("editorial_team_abbr"),
                "is_starter": slot != BENCH_SLOT and slot not in IR_SLOTS,
                "actual_points": None,
                "projected_points": None,
            }
        )
    return roster_players


def _get_scoreboard(league_key: str, access_token: str) -> tuple[int | None, list[list[dict]]]:
    """Returns (week, matchups), where each matchup is
    [{"team_key", "points"}, {"team_key", "points"}]."""
    data = _get(f"{YAHOO_FANTASY_BASE_URL}/league/{league_key}/scoreboard", access_token)
    league_data = _navigate(data.get("fantasy_content"), "league")
    scoreboard_raw = _reformat(league_data).get("scoreboard")
    matchups_raw = _find_collection(scoreboard_raw, "matchups")
    week = None

    matchups = []
    for m_entry in _unwrap_list(matchups_raw):
        matchup = _reformat(_reformat(m_entry).get("matchup"))
        if week is None and matchup.get("week") is not None:
            week = int(matchup["week"])
        teams_raw = _find_collection(matchup, "teams")
        pair = []
        for t_entry in _unwrap_list(teams_raw):
            team_entries = _reformat(t_entry).get("team")
            flat = _reformat(team_entries)
            points = _reformat(flat.get("team_points")).get("total")
            pair.append({"team_key": flat.get("team_key"), "points": float(points or 0.0)})
        if len(pair) == 2:
            matchups.append(pair)
    return week, matchups


def normalize_league(league_id: str, access_token: str, my_guid: str) -> dict:
    """Fetch everything for one Yahoo league and normalize into
    LeagueDeck's shared League/Team shape (the same shape sleeper.
    normalize_league/espn.normalize_league produce)."""
    league_key = _league_key(league_id)
    metadata = _get_league_metadata(league_key, access_token)
    teams = _get_league_teams(league_key, access_token)
    week, matchups = _get_scoreboard(league_key, access_token)

    if week is None:
        current_week_raw = metadata.get("current_week")
        if current_week_raw is not None:
            try:
                week = int(current_week_raw)
            except (TypeError, ValueError):
                pass

    if week is None:
        logger.warning(
            "yahoo: could not determine current week for league %s -- possible "
            "scoreboard schema drift",
            league_id,
        )
    if teams and not matchups:
        logger.warning(
            "yahoo: resolved 0 matchups for %d teams in league %s -- possible "
            "scoreboard schema drift",
            len(teams),
            league_id,
        )

    points_by_team_key: dict[str, float] = {}
    opponent_by_team_key: dict[str, tuple[str | None, float | None]] = {}
    for pair in matchups:
        (a, b) = pair
        points_by_team_key[a["team_key"]] = a["points"]
        points_by_team_key[b["team_key"]] = b["points"]
        opponent_by_team_key[a["team_key"]] = (b["team_key"], b["points"])
        opponent_by_team_key[b["team_key"]] = (a["team_key"], a["points"])

    teams_by_key = {t["team_key"]: t for t in teams}

    result_teams = []
    for team in teams:
        team_key = team["team_key"]
        opponent_key, opponent_points = opponent_by_team_key.get(team_key, (None, None))
        opponent_team = teams_by_key.get(opponent_key)
        roster_players = _get_team_roster(team_key, week, access_token)
        result_teams.append(
            {
                "platform_team_id": team_key,
                "name": team["name"],
                "is_mine": my_guid in team["manager_guids"],
                "roster_json": roster_players,
                "points_for": points_by_team_key.get(team_key, 0.0),
                "opponent_name": opponent_team["name"] if opponent_team else None,
                "opponent_points": opponent_points,
                "week": week,
            }
        )

    return {
        "platform": "yahoo",
        "platform_league_id": league_id,
        "name": metadata.get("name") or f"Yahoo League {league_id}",
        "season": metadata.get("season") or "",
        "teams": result_teams,
    }
