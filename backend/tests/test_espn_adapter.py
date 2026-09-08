import httpx

from app.adapters import espn


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def _combined_response(**overrides):
    """A minimal but representative combined mTeam/mRoster/mMatchup/mSettings
    response: two teams, one roster entry each, one scored matchup between them."""
    base = {
        "scoringPeriodId": 3,
        "settings": {"name": "Test League"},
        "teams": [
            {
                "id": 1,
                "location": "Team",
                "nickname": "One",
                "owners": ["{ABC-123}"],
                "roster": {
                    "entries": [
                        {
                            "lineupSlotId": 0,
                            "playerPoolEntry": {
                                "player": {
                                    "id": 111,
                                    "fullName": "Player One",
                                    "defaultPositionId": 2,
                                    "proTeamId": 12,
                                }
                            },
                        },
                        {
                            "lineupSlotId": 20,
                            "playerPoolEntry": {
                                "player": {
                                    "id": 222,
                                    "fullName": "Bench Guy",
                                    "defaultPositionId": 3,
                                    "proTeamId": 0,
                                }
                            },
                        },
                    ]
                },
            },
            {
                "id": 2,
                "location": "Team",
                "nickname": "Two",
                "owners": ["{XYZ-999}"],
                "roster": {"entries": []},
            },
        ],
        "schedule": [
            {
                "matchupPeriodId": 3,
                "home": {"teamId": 1, "totalPoints": 100.5},
                "away": {"teamId": 2, "totalPoints": 90.2},
            }
        ],
    }
    base.update(overrides)
    return base


def test_normalize_league_builds_teams_with_mapped_position_and_team(monkeypatch):
    def fake_get(url, params=None, cookies=None, timeout=10.0):
        assert url == f"{espn.ESPN_BASE_URL}/2026/segments/0/leagues/999"
        assert cookies == {"espn_s2": "s2val", "SWID": "{ABC-123}"}
        assert ("view", "mTeam") in params
        assert ("view", "mRoster") in params
        assert ("view", "mMatchup") in params
        assert ("view", "mSettings") in params
        return FakeResponse(_combined_response())

    monkeypatch.setattr(httpx, "get", fake_get)

    result = espn.normalize_league("999", 2026, "s2val", "{ABC-123}")

    assert result["platform"] == "espn"
    assert result["platform_league_id"] == "999"
    assert result["name"] == "Test League"
    assert result["season"] == "2026"  # cast to str even though 2026 (int) was passed in

    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["platform_team_id"] == "1"
    assert my_team["name"] == "Team One"
    assert my_team["points_for"] == 100.5
    assert my_team["opponent_name"] == "Team Two"
    assert my_team["opponent_points"] == 90.2
    assert my_team["week"] == 3

    starter = next(p for p in my_team["roster_json"] if p["player_id"] == "111")
    assert starter == {
        "player_id": "111",
        "name": "Player One",
        "position": "RB",
        "team": "KC",
        "is_starter": True,
    }
    bench = next(p for p in my_team["roster_json"] if p["player_id"] == "222")
    assert bench["position"] == "WR"
    assert bench["team"] is None  # proTeamId 0 (free agent) maps to None, not "FA"
    assert bench["is_starter"] is False  # lineupSlotId 20 == bench


def test_normalize_league_maps_defense_slot_to_def_not_dst(monkeypatch):
    """ESPN's own label for this position is 'D/ST'. The frontend's
    POSITION_ORDER array is Sleeper's vocabulary and only recognizes 'DEF' --
    an unmapped 'D/ST' would silently sort to the bottom of every roster."""
    response = _combined_response()
    response["teams"][0]["roster"]["entries"] = [
        {
            "lineupSlotId": 16,
            "playerPoolEntry": {
                "player": {
                    "id": 333,
                    "fullName": "Chiefs D/ST",
                    "defaultPositionId": 16,
                    "proTeamId": 12,
                }
            },
        }
    ]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(response))

    result = espn.normalize_league("999", 2026, "s2val", "{ABC-123}")
    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["roster_json"][0]["position"] == "DEF"


def test_normalize_league_excludes_ir_slot_from_starters(monkeypatch):
    response = _combined_response()
    response["teams"][0]["roster"]["entries"] = [
        {
            "lineupSlotId": 21,  # IR
            "playerPoolEntry": {
                "player": {
                    "id": 444,
                    "fullName": "Hurt Guy",
                    "defaultPositionId": 1,
                    "proTeamId": 7,
                }
            },
        }
    ]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(response))

    result = espn.normalize_league("999", 2026, "s2val", "{ABC-123}")
    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["roster_json"][0]["is_starter"] is False


def test_normalize_league_matches_swid_case_and_brace_insensitively(monkeypatch):
    """A user pasting from DevTools may omit braces or differ in case from
    what ESPN echoes back in `owners` -- the comparison must not be a naive =="""
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(_combined_response()))

    result = espn.normalize_league("999", 2026, "s2val", "abc-123")  # no braces, lowercase
    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["platform_team_id"] == "1"


def test_normalize_league_prefers_explicit_name_over_location_nickname(monkeypatch):
    response = _combined_response()
    response["teams"][0]["name"] = "Explicit Team Name"
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(response))

    result = espn.normalize_league("999", 2026, "s2val", "{ABC-123}")
    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["name"] == "Explicit Team Name"


def test_normalize_league_handles_team_with_no_scheduled_game(monkeypatch):
    """An odd-team league (or a bye week) can leave a team with no schedule
    entry at all for the current week -- own points default to 0.0, no
    opponent, matching Sleeper's convention for the same situation."""
    response = _combined_response()
    response["schedule"] = []
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(response))

    result = espn.normalize_league("999", 2026, "s2val", "{ABC-123}")
    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["points_for"] == 0.0
    assert my_team["opponent_name"] is None
    assert my_team["opponent_points"] is None


def test_normalize_league_falls_back_to_player_id_when_name_missing(monkeypatch):
    response = _combined_response()
    response["teams"][0]["roster"]["entries"] = [
        {
            "lineupSlotId": 0,
            "playerPoolEntry": {
                "player": {"id": 555, "defaultPositionId": 5, "proTeamId": 99}
            },
        }
    ]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResponse(response))

    result = espn.normalize_league("999", 2026, "s2val", "{ABC-123}")
    my_team = next(t for t in result["teams"] if t["is_mine"])
    player = my_team["roster_json"][0]
    assert player["name"] == "555"
    assert player["position"] == "K"
    assert player["team"] is None  # unknown proTeamId (99) also falls back to None
