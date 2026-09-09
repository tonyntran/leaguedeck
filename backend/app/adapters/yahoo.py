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
