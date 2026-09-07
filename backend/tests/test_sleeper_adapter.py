import httpx

from app.adapters import sleeper


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def test_get_user_id_returns_user_id(monkeypatch):
    def fake_get(url, timeout=10.0):
        assert url == f"{sleeper.SLEEPER_BASE_URL}/user/testuser"
        return FakeResponse({"user_id": "12345"})

    monkeypatch.setattr(httpx, "get", fake_get)
    assert sleeper.get_user_id("testuser") == "12345"


def test_normalize_league_builds_teams_with_opponent_and_roster(monkeypatch):
    league_id = "999"
    responses = {
        f"{sleeper.SLEEPER_BASE_URL}/league/{league_id}": FakeResponse(
            {"name": "Test League", "season": "2026", "settings": {"leg": 1}}
        ),
        f"{sleeper.SLEEPER_BASE_URL}/league/{league_id}/rosters": FakeResponse(
            [
                {"roster_id": 1, "owner_id": "u1", "players": ["p1"]},
                {"roster_id": 2, "owner_id": "u2", "players": ["p2"]},
            ]
        ),
        f"{sleeper.SLEEPER_BASE_URL}/league/{league_id}/users": FakeResponse(
            [
                {"user_id": "u1", "display_name": "Me", "metadata": {}},
                {"user_id": "u2", "display_name": "Rival", "metadata": {}},
            ]
        ),
        f"{sleeper.SLEEPER_BASE_URL}/league/{league_id}/matchups/1": FakeResponse(
            [
                {"roster_id": 1, "matchup_id": 1, "points": 100.5},
                {"roster_id": 2, "matchup_id": 1, "points": 90.2},
            ]
        ),
    }

    def fake_get(url, timeout=10.0):
        return responses[url]

    monkeypatch.setattr(httpx, "get", fake_get)

    players_map = {"p1": {"full_name": "Player One", "position": "RB", "team": "KC"}}
    result = sleeper.normalize_league(league_id, my_user_id="u1", players_map=players_map)

    assert result["name"] == "Test League"
    assert len(result["teams"]) == 2
    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["name"] == "Me"
    assert my_team["opponent_name"] == "Rival"
    assert my_team["opponent_points"] == 90.2
    assert my_team["roster_json"] == [
        {"player_id": "p1", "name": "Player One", "position": "RB", "team": "KC"}
    ]


def _fake_get_from(responses):
    def fake_get(url, timeout=10.0):
        return responses[url]

    return fake_get


def test_normalize_league_pairs_opponents_by_matchup_id_not_list_order(monkeypatch):
    """Four rosters across two matchups, interleaved in the matchups list so that
    pairing by list order (or 'the other team') yields the wrong opponent."""
    league_id = "777"
    base = sleeper.SLEEPER_BASE_URL
    responses = {
        f"{base}/league/{league_id}": FakeResponse(
            {"name": "Big League", "season": "2026", "settings": {"leg": 7}}
        ),
        f"{base}/league/{league_id}/rosters": FakeResponse(
            [
                {"roster_id": 1, "owner_id": "u1", "players": ["p1", "pX"]},
                {"roster_id": 2, "owner_id": "u2", "players": []},
                {"roster_id": 3, "owner_id": "u3", "players": ["p2"]},
                {"roster_id": 4, "owner_id": "u4", "players": None},
            ]
        ),
        f"{base}/league/{league_id}/users": FakeResponse(
            [
                {"user_id": "u1", "display_name": "Me", "metadata": {}},
                {
                    "user_id": "u2",
                    "display_name": "TwoDisplay",
                    "metadata": {"team_name": "Team Two"},
                },
                {"user_id": "u3", "display_name": "Three", "metadata": {}},
                {"user_id": "u4", "display_name": "Four", "metadata": {}},
            ]
        ),
        # Roster 1 is paired with 3, and roster 2 with 4 -- but they are interleaved.
        f"{base}/league/{league_id}/matchups/7": FakeResponse(
            [
                {"roster_id": 1, "matchup_id": 1, "points": 10.0},
                {"roster_id": 2, "matchup_id": 2, "points": 20.0},
                {"roster_id": 3, "matchup_id": 1, "points": 30.0},
                {"roster_id": 4, "matchup_id": 2, "points": 40.0},
            ]
        ),
    }
    monkeypatch.setattr(httpx, "get", _fake_get_from(responses))

    players_map = {
        "p1": {"full_name": "Player One", "position": "RB", "team": "KC"},
        "p2": {"full_name": "Player Two", "position": "WR", "team": "SF"},
    }
    result = sleeper.normalize_league(league_id, my_user_id="u1", players_map=players_map)

    by_id = {t["platform_team_id"]: t for t in result["teams"]}
    assert [t["week"] for t in result["teams"]] == [7, 7, 7, 7]

    assert by_id["1"]["opponent_name"] == "Three"
    assert by_id["1"]["opponent_points"] == 30.0
    assert by_id["1"]["points_for"] == 10.0
    assert by_id["1"]["is_mine"] is True

    assert by_id["3"]["opponent_name"] == "Me"
    assert by_id["3"]["opponent_points"] == 10.0

    assert by_id["2"]["opponent_name"] == "Four"
    assert by_id["2"]["opponent_points"] == 40.0
    assert by_id["4"]["opponent_name"] == "Team Two"
    assert by_id["4"]["opponent_points"] == 20.0

    # metadata team_name wins over display_name
    assert by_id["2"]["name"] == "Team Two"
    # only roster 1's owner is mine
    assert [t["platform_team_id"] for t in result["teams"] if t["is_mine"]] == ["1"]
    # unknown player ids fall back to the raw id; a null players list is empty
    assert by_id["1"]["roster_json"][1] == {
        "player_id": "pX",
        "name": "pX",
        "position": None,
        "team": None,
    }
    assert by_id["4"]["roster_json"] == []


def test_normalize_league_does_not_pair_bye_week_rosters(monkeypatch):
    """Sleeper returns a row for every roster every week, with `matchup_id: null`
    for rosters not scheduled that week (playoff byes, consolation gaps, odd team
    counts). Those rows must not be grouped together into a fabricated game."""
    league_id = "333"
    base = sleeper.SLEEPER_BASE_URL
    responses = {
        f"{base}/league/{league_id}": FakeResponse(
            {"name": "Playoff League", "season": "2026", "settings": {"leg": 15}}
        ),
        f"{base}/league/{league_id}/rosters": FakeResponse(
            [
                {"roster_id": 1, "owner_id": "u1", "players": []},
                {"roster_id": 2, "owner_id": "u2", "players": []},
                {"roster_id": 3, "owner_id": "u3", "players": []},
                {"roster_id": 4, "owner_id": "u4", "players": []},
            ]
        ),
        f"{base}/league/{league_id}/users": FakeResponse(
            [
                {"user_id": "u1", "display_name": "Bye One", "metadata": {}},
                {"user_id": "u2", "display_name": "Bye Two", "metadata": {}},
                {"user_id": "u3", "display_name": "Three", "metadata": {}},
                {"user_id": "u4", "display_name": "Four", "metadata": {}},
            ]
        ),
        # Rosters 1 and 2 are both on byes; only 3 vs 4 is a real game.
        f"{base}/league/{league_id}/matchups/15": FakeResponse(
            [
                {"roster_id": 1, "matchup_id": None, "points": 12.5},
                {"roster_id": 2, "matchup_id": None, "points": 33.3},
                {"roster_id": 3, "matchup_id": 5, "points": 88.0},
                {"roster_id": 4, "matchup_id": 5, "points": 77.0},
            ]
        ),
    }
    monkeypatch.setattr(httpx, "get", _fake_get_from(responses))

    result = sleeper.normalize_league(league_id, my_user_id="u1", players_map={})
    by_id = {t["platform_team_id"]: t for t in result["teams"]}

    # Neither bye roster may be shown as playing the other (or anyone).
    for bye_id, pts in (("1", 12.5), ("2", 33.3)):
        assert by_id[bye_id]["opponent_name"] is None
        assert by_id[bye_id]["opponent_points"] is None
        assert by_id[bye_id]["points_for"] == pts

    # The real game is unaffected.
    assert by_id["3"]["opponent_name"] == "Four"
    assert by_id["3"]["opponent_points"] == 77.0
    assert by_id["4"]["opponent_name"] == "Three"
    assert by_id["4"]["opponent_points"] == 88.0


def test_normalize_league_handles_singleton_matchup_group(monkeypatch):
    """A matchup group with only one roster in it (odd team count) has no
    opponent to pair against."""
    league_id = "222"
    base = sleeper.SLEEPER_BASE_URL
    responses = {
        f"{base}/league/{league_id}": FakeResponse(
            {"name": "Odd League", "season": "2026", "settings": {"leg": 4}}
        ),
        f"{base}/league/{league_id}/rosters": FakeResponse(
            [{"roster_id": 9, "owner_id": "u9", "players": []}]
        ),
        f"{base}/league/{league_id}/users": FakeResponse(
            [{"user_id": "u9", "display_name": "Lonely", "metadata": {}}]
        ),
        f"{base}/league/{league_id}/matchups/4": FakeResponse(
            [{"roster_id": 9, "matchup_id": 3, "points": 55.0}]
        ),
    }
    monkeypatch.setattr(httpx, "get", _fake_get_from(responses))

    result = sleeper.normalize_league(league_id, my_user_id="u9", players_map={})
    (team,) = result["teams"]
    assert team["name"] == "Lonely"
    assert team["points_for"] == 55.0  # own points still reported
    assert team["opponent_name"] is None
    assert team["opponent_points"] is None


def test_normalize_league_handles_null_metadata_on_users(monkeypatch):
    """Sleeper sends `metadata: null` for members who never set a team name.
    A dict .get default does not cover that, so both the team's own name and the
    opponent's name must coalesce it."""
    league_id = "444"
    base = sleeper.SLEEPER_BASE_URL
    responses = {
        f"{base}/league/{league_id}": FakeResponse(
            {"name": "Null League", "season": "2026", "settings": {"leg": 3}}
        ),
        f"{base}/league/{league_id}/rosters": FakeResponse(
            [
                {"roster_id": 1, "owner_id": "u1", "players": []},
                {"roster_id": 2, "owner_id": "u2", "players": []},
            ]
        ),
        f"{base}/league/{league_id}/users": FakeResponse(
            [
                {"user_id": "u1", "display_name": "Me", "metadata": None},
                {"user_id": "u2", "display_name": "Rival", "metadata": None},
            ]
        ),
        f"{base}/league/{league_id}/matchups/3": FakeResponse(
            [
                {"roster_id": 1, "matchup_id": 1, "points": 1.0},
                {"roster_id": 2, "matchup_id": 1, "points": 2.0},
            ]
        ),
    }
    monkeypatch.setattr(httpx, "get", _fake_get_from(responses))

    result = sleeper.normalize_league(league_id, my_user_id="u1", players_map={})
    my_team = next(t for t in result["teams"] if t["is_mine"])
    assert my_team["name"] == "Me"
    assert my_team["opponent_name"] == "Rival"


def test_normalize_league_handles_week_with_no_matchups(monkeypatch):
    """Sleeper returns an empty matchups list outside the regular season."""
    league_id = "555"
    base = sleeper.SLEEPER_BASE_URL
    responses = {
        f"{base}/league/{league_id}": FakeResponse(
            {"name": "Quiet League", "season": "2026", "settings": {"leg": 2}}
        ),
        f"{base}/league/{league_id}/rosters": FakeResponse(
            [{"roster_id": 1, "owner_id": "u1", "players": ["p1"]}]
        ),
        f"{base}/league/{league_id}/users": FakeResponse(
            [{"user_id": "u1", "display_name": "Me", "metadata": {}}]
        ),
        f"{base}/league/{league_id}/matchups/2": FakeResponse([]),
    }
    monkeypatch.setattr(httpx, "get", _fake_get_from(responses))

    result = sleeper.normalize_league(league_id, my_user_id="u1", players_map={})
    (team,) = result["teams"]
    assert team["points_for"] == 0.0
    assert team["opponent_name"] is None
    assert team["opponent_points"] is None
    assert team["week"] == 2
