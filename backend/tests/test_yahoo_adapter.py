import base64

import httpx

from app.adapters import yahoo


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def test_unwrap_list_converts_indexed_dict_to_list():
    assert yahoo._unwrap_list({"0": "a", "1": "b", "count": 2}) == ["a", "b"]


def test_unwrap_list_passes_through_a_real_list_unchanged():
    assert yahoo._unwrap_list(["a", "b"]) == ["a", "b"]


def test_unwrap_list_unwraps_a_single_wrapped_value():
    """XML has no distinct 'definitely one item' representation, so a lone
    value can arrive wrapped as {"0": value} with no "count" sibling."""
    assert yahoo._unwrap_list({"0": {"key": "value"}}) == [{"key": "value"}]


def test_unwrap_list_returns_empty_list_for_anything_else():
    assert yahoo._unwrap_list(None) == []
    assert yahoo._unwrap_list("not a collection") == []


def test_reformat_merges_a_list_of_single_key_dicts():
    """Mixed sibling elements at the same XML level (e.g. <league> next to
    <time> next to <copyright>) render as a genuine JSON array of
    single-key dicts, not an object -- this must merge into one dict."""
    assert yahoo._reformat([{"league": {"name": "Test"}}, {"time": "1.2"}]) == {
        "league": {"name": "Test"},
        "time": "1.2",
    }


def test_reformat_passes_through_a_dict_unchanged():
    assert yahoo._reformat({"league": {"name": "Test"}}) == {"league": {"name": "Test"}}


def test_reformat_returns_empty_dict_for_anything_else():
    assert yahoo._reformat(None) == {}
    assert yahoo._reformat("not a mapping") == {}


def test_navigate_walks_nested_keys_through_mixed_shapes():
    fantasy_content = [
        {"league": [{"name": "Test League"}, {"season": "2026"}]},
        {"time": "0.1"},
    ]
    assert yahoo._navigate(fantasy_content, "league") == [
        {"name": "Test League"},
        {"season": "2026"},
    ]


def test_get_authorize_url_builds_oob_request():
    url = yahoo.get_authorize_url("my-client-id")
    assert url == (
        "https://api.login.yahoo.com/oauth2/request_auth"
        "?client_id=my-client-id&redirect_uri=oob&response_type=code"
    )


def test_exchange_code_for_tokens_sends_basic_auth_and_parses_response(monkeypatch):
    def fake_post(url, headers=None, data=None, timeout=10.0):
        assert url == "https://api.login.yahoo.com/oauth2/get_token"
        expected_token = base64.b64encode(b"client123:secret456").decode()
        assert headers == {"Authorization": f"Basic {expected_token}"}
        assert data == {
            "redirect_uri": "oob",
            "code": "the-code",
            "grant_type": "authorization_code",
        }
        return FakeResponse(
            {
                "access_token": "at-1",
                "refresh_token": "rt-1",
                "expires_in": 3600,
                "xoauth_yahoo_guid": "GUID123",
            }
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    result = yahoo.exchange_code_for_tokens("client123", "secret456", "the-code")
    assert result == {
        "access_token": "at-1",
        "refresh_token": "rt-1",
        "expires_in": 3600,
        "yahoo_guid": "GUID123",
    }


def test_refresh_access_token_sends_refresh_grant_and_redirect_uri(monkeypatch):
    """redirect_uri=oob is required on the refresh grant too -- easy to
    omit by mistake, and omitting it risks an error indistinguishable from
    an expired refresh token."""
    def fake_post(url, headers=None, data=None, timeout=10.0):
        assert data == {
            "redirect_uri": "oob",
            "refresh_token": "old-refresh",
            "grant_type": "refresh_token",
        }
        return FakeResponse(
            {"access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600}
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    result = yahoo.refresh_access_token("client123", "secret456", "old-refresh")
    assert result["access_token"] == "at-2"
    assert result["refresh_token"] == "rt-2"
    assert result["yahoo_guid"] is None  # not present on this response


def test_exchange_code_for_tokens_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: FakeResponse({"error": "invalid_grant"}, status_code=400)
    )

    raised = False
    try:
        yahoo.exchange_code_for_tokens("client123", "secret456", "bad-code")
    except httpx.HTTPStatusError:
        raised = True
    assert raised


def _fantasy_content(league_body):
    """Wraps a league response body the way Yahoo's top-level envelope
    does: fantasy_content as a list of single-key dicts (mixed sibling
    elements), one of which is "league"."""
    return {"fantasy_content": [{"league": league_body}]}


def _league_metadata_response():
    return _fantasy_content(
        [
            {"name": "Test League", "season": "2026", "current_week": "3"},
        ]
    )


def _league_teams_response():
    return _fantasy_content(
        [
            {"name": "Test League"},
            {
                "teams": {
                    "0": {
                        "team": [
                            {"team_key": "nfl.l.999.t.1", "name": "Team One"},
                            {"managers": {"0": {"manager": {"guid": "MY-GUID"}}}},
                        ]
                    },
                    "1": {
                        "team": [
                            {"team_key": "nfl.l.999.t.2", "name": "Team Two"},
                            {"managers": {"0": {"manager": {"guid": "OTHER-GUID"}}}},
                        ]
                    },
                    "count": 2,
                }
            },
        ]
    )


def _roster_with_players(entries):
    return {
        "fantasy_content": [
            {
                "team": [
                    {"name": "Team One"},
                    {
                        "roster": {
                            "0": {
                                "players": {
                                    **{str(i): {"player": e} for i, e in enumerate(entries)},
                                    "count": len(entries),
                                }
                            }
                        }
                    },
                ]
            }
        ]
    }


def _player_entry(player_id, name, position, pro_team, slot):
    return [
        {
            "player_id": player_id,
            "name": {"full": name},
            "display_position": position,
            "editorial_team_abbr": pro_team,
        },
        {"selected_position": [{"position": slot}]},
    ]


def _scoreboard_response(week, team_key_a, points_a, team_key_b, points_b):
    return {
        "fantasy_content": [
            {
                "league": [
                    {"name": "unused"},
                    {
                        "scoreboard": {
                            "0": {
                                "matchups": {
                                    "0": {
                                        "matchup": {
                                            "week": week,
                                            "teams": {
                                                "0": {
                                                    "team": [
                                                        {"team_key": team_key_a},
                                                        {"team_points": {"total": points_a}},
                                                    ]
                                                },
                                                "1": {
                                                    "team": [
                                                        {"team_key": team_key_b},
                                                        {"team_points": {"total": points_b}},
                                                    ]
                                                },
                                                "count": 2,
                                            },
                                        }
                                    },
                                    "count": 1,
                                }
                            }
                        }
                    },
                ]
            }
        ]
    }


def _scoreboard_response_no_matchups():
    """A scoreboard response with zero matchups -- e.g. before the season's
    first matchups have been created, or under the same kind of schema
    drift the Task 2 fix round's warning anticipates."""
    return {
        "fantasy_content": [
            {
                "league": [
                    {"name": "unused"},
                    {"scoreboard": {"0": {"matchups": {"count": 0}}}},
                ]
            }
        ]
    }


def _league_metadata_response_no_current_week():
    return _fantasy_content(
        [
            {"name": "Test League", "season": "2026"},
        ]
    )


def test_normalize_league_degrades_gracefully_when_week_unresolvable(monkeypatch):
    """When neither the scoreboard nor the league metadata yields a current
    week, normalize_league must still return team/roster data instead of
    raising -- and _get_team_roster must fetch the roster URL with no
    ;week= segment (a literal ";week=None" would be rejected by Yahoo)."""
    league_id = "999"
    responses = {
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/metadata": _league_metadata_response_no_current_week(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/teams": _league_teams_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/scoreboard": _scoreboard_response_no_matchups(),
        # Deliberately registered with no ";week=" suffix -- if
        # _get_team_roster still builds ".../roster;week=None", the lookup
        # below raises KeyError and fails the test.
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.1/roster": _roster_with_players(
            [_player_entry("111", "Player One", "RB", "KC", "QB")]
        ),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.2/roster": _roster_with_players([]),
    }
    monkeypatch.setattr(httpx, "get", lambda url, headers=None, timeout=15.0: FakeResponse(responses[url]))

    result = yahoo.normalize_league(league_id, "test-access-token", "MY-GUID")

    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["week"] is None
    assert my_team["points_for"] == 0.0
    assert my_team["opponent_name"] is None
    assert my_team["roster_json"][0]["name"] == "Player One"


def test_normalize_league_builds_teams_with_roster_and_score(monkeypatch):
    league_id = "999"
    my_guid = "MY-GUID"

    responses = {
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/metadata": _league_metadata_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/teams": _league_teams_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/scoreboard": _scoreboard_response(
            "3", "nfl.l.999.t.1", "100.5", "nfl.l.999.t.2", "90.2"
        ),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.1/roster;week=3": _roster_with_players(
            [_player_entry("111", "Player One", "RB", "KC", "QB")]
        ),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.2/roster;week=3": _roster_with_players([]),
    }

    def fake_get(url, headers=None, timeout=15.0):
        assert headers == {"Authorization": "Bearer test-access-token"}
        return FakeResponse(responses[url])

    monkeypatch.setattr(httpx, "get", fake_get)

    result = yahoo.normalize_league(league_id, "test-access-token", my_guid)

    assert result["platform"] == "yahoo"
    assert result["platform_league_id"] == "999"
    assert result["name"] == "Test League"
    assert result["season"] == "2026"

    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["platform_team_id"] == "nfl.l.999.t.1"
    assert my_team["name"] == "Team One"
    assert my_team["points_for"] == 100.5
    assert my_team["opponent_name"] == "Team Two"
    assert my_team["opponent_points"] == 90.2
    assert my_team["week"] == 3

    player = my_team["roster_json"][0]
    assert player == {
        "player_id": "111",
        "name": "Player One",
        "position": "RB",
        "team": "KC",
        "is_starter": True,
        "actual_points": None,
        "projected_points": None,
    }

    other_team = next(t for t in result["teams"] if not t["is_mine"])
    assert other_team["name"] == "Team Two"


def test_normalize_league_marks_bench_slot_as_not_starting(monkeypatch):
    league_id = "999"
    responses = {
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/metadata": _league_metadata_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/teams": _league_teams_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/scoreboard": _scoreboard_response(
            "3", "nfl.l.999.t.1", "0", "nfl.l.999.t.2", "0"
        ),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.1/roster;week=3": _roster_with_players(
            [_player_entry("222", "Bench Guy", "WR", "SF", "BN")]
        ),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.2/roster;week=3": _roster_with_players([]),
    }
    monkeypatch.setattr(httpx, "get", lambda url, headers=None, timeout=15.0: FakeResponse(responses[url]))

    result = yahoo.normalize_league(league_id, "test-access-token", "MY-GUID")
    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["roster_json"][0]["is_starter"] is False


def test_normalize_league_marks_ir_slot_as_not_starting(monkeypatch):
    league_id = "999"
    responses = {
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/metadata": _league_metadata_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/teams": _league_teams_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/scoreboard": _scoreboard_response(
            "3", "nfl.l.999.t.1", "0", "nfl.l.999.t.2", "0"
        ),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.1/roster;week=3": _roster_with_players(
            [_player_entry("333", "Injured Guy", "RB", "SF", "IR")]
        ),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.2/roster;week=3": _roster_with_players([]),
    }
    monkeypatch.setattr(httpx, "get", lambda url, headers=None, timeout=15.0: FakeResponse(responses[url]))

    result = yahoo.normalize_league(league_id, "test-access-token", "MY-GUID")
    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["roster_json"][0]["is_starter"] is False


def test_normalize_league_casts_string_points_and_week_to_correct_types(monkeypatch):
    """Every value in Yahoo's JSON arrives as a string -- this must not
    leak a str into Team.points_for (Float) or Team.week (Integer)."""
    league_id = "999"
    responses = {
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/metadata": _league_metadata_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/teams": _league_teams_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/scoreboard": _scoreboard_response(
            "3", "nfl.l.999.t.1", "123.45", "nfl.l.999.t.2", "67.89"
        ),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.1/roster;week=3": _roster_with_players([]),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.2/roster;week=3": _roster_with_players([]),
    }
    monkeypatch.setattr(httpx, "get", lambda url, headers=None, timeout=15.0: FakeResponse(responses[url]))

    result = yahoo.normalize_league(league_id, "test-access-token", "MY-GUID")
    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["points_for"] == 123.45
    assert isinstance(my_team["points_for"], float)
    assert my_team["week"] == 3
    assert isinstance(my_team["week"], int)


def test_normalize_league_matches_guid_for_is_mine_not_a_team_flag(monkeypatch):
    """is_mine must come from comparing the authenticated user's own GUID
    against each team's manager GUIDs -- never a team-level flag."""
    league_id = "999"
    responses = {
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/metadata": _league_metadata_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/teams": _league_teams_response(),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/league/nfl.l.{league_id}/scoreboard": _scoreboard_response(
            "3", "nfl.l.999.t.1", "0", "nfl.l.999.t.2", "0"
        ),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.1/roster;week=3": _roster_with_players([]),
        f"{yahoo.YAHOO_FANTASY_BASE_URL}/team/nfl.l.999.t.2/roster;week=3": _roster_with_players([]),
    }
    monkeypatch.setattr(httpx, "get", lambda url, headers=None, timeout=15.0: FakeResponse(responses[url]))

    # Authenticate as the SECOND team's manager this time.
    result = yahoo.normalize_league(league_id, "test-access-token", "OTHER-GUID")
    assert [t["platform_team_id"] for t in result["teams"] if t["is_mine"]] == ["nfl.l.999.t.2"]
