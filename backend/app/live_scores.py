import logging
import time

import httpx

logger = logging.getLogger(__name__)

# ESPN's public site API, not the private fantasy API used elsewhere in this
# app -- no cookies, no OAuth, no per-league key. Same host that powers
# espn.com's own live scoreboard.
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
SCOREBOARD_CACHE_MAX_AGE_SECONDS = 30

_cache = {"data": None, "fetched_at": 0.0}


def _fetch_scoreboard() -> dict:
    """Cached briefly since the dashboard's 60s auto-refresh (and any extra
    browser tab) can trigger this well more often than the scoreboard
    actually changes -- a good-citizen cache, not a correctness requirement."""
    now = time.time()
    if _cache["data"] is not None and (now - _cache["fetched_at"]) < SCOREBOARD_CACHE_MAX_AGE_SECONDS:
        return _cache["data"]

    resp = httpx.get(ESPN_SCOREBOARD_URL, timeout=10.0)
    resp.raise_for_status()
    data = resp.json()
    _cache["data"] = data
    _cache["fetched_at"] = now
    return data


def get_live_games(relevant_teams: set[str]) -> list[dict]:
    """Returns in-progress NFL games where either team's abbreviation is in
    relevant_teams. Games that haven't started or have already finished are
    excluded -- this is specifically a "what's live right now" list, not a
    full day's schedule."""
    data = _fetch_scoreboard()
    games = []
    for event in data.get("events") or []:
        state = ((event.get("status") or {}).get("type") or {}).get("state")
        if state != "in":
            continue

        competitions = event.get("competitions") or []
        if not competitions:
            continue
        competitors = competitions[0].get("competitors") or []
        by_home_away = {c.get("homeAway"): c for c in competitors}
        home, away = by_home_away.get("home") or {}, by_home_away.get("away") or {}
        home_abbr = (home.get("team") or {}).get("abbreviation")
        away_abbr = (away.get("team") or {}).get("abbreviation")
        if home_abbr not in relevant_teams and away_abbr not in relevant_teams:
            continue

        games.append(
            {
                "home_team": home_abbr,
                "away_team": away_abbr,
                "home_score": int(home.get("score") or 0),
                "away_score": int(away.get("score") or 0),
                "state": state,
                "detail": ((event.get("status") or {}).get("type") or {}).get("shortDetail"),
            }
        )
    return games
