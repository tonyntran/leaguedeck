import httpx

from app import live_scores


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


def _event(home_abbr, home_score, away_abbr, away_score, state="in", detail="10:23 - 2nd Quarter"):
    return {
        "status": {"type": {"state": state, "shortDetail": detail}},
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": {"abbreviation": home_abbr}, "score": str(home_score)},
                    {"homeAway": "away", "team": {"abbreviation": away_abbr}, "score": str(away_score)},
                ]
            }
        ],
    }


def _reset_cache():
    live_scores._cache["data"] = None
    live_scores._cache["fetched_at"] = 0.0


def test_get_live_games_filters_to_relevant_teams(monkeypatch):
    _reset_cache()

    def fake_get(url, timeout=10.0):
        assert url == live_scores.ESPN_SCOREBOARD_URL
        return FakeResponse(
            {"events": [_event("DEN", 14, "LAC", 10), _event("SF", 21, "SEA", 17)]}
        )

    monkeypatch.setattr(httpx, "get", fake_get)

    result = live_scores.get_live_games({"DEN"})
    assert len(result) == 1
    assert result[0]["home_team"] == "DEN"
    assert result[0]["away_team"] == "LAC"
    assert result[0]["home_score"] == 14
    assert result[0]["away_score"] == 10
    assert result[0]["state"] == "in"
    assert result[0]["detail"] == "10:23 - 2nd Quarter"


def test_get_live_games_excludes_games_not_in_progress(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(
        httpx, "get", lambda url, timeout=10.0: FakeResponse({"events": [_event("DEN", 0, "LAC", 0, state="pre")]})
    )

    assert live_scores.get_live_games({"DEN"}) == []


def test_get_live_games_caches_across_calls(monkeypatch):
    _reset_cache()
    calls = {"n": 0}

    def fake_get(url, timeout=10.0):
        calls["n"] += 1
        return FakeResponse({"events": [_event("DEN", 14, "LAC", 10)]})

    monkeypatch.setattr(httpx, "get", fake_get)

    live_scores.get_live_games({"DEN"})
    live_scores.get_live_games({"DEN"})
    assert calls["n"] == 1


def test_get_live_games_returns_empty_list_for_no_relevant_teams(monkeypatch):
    _reset_cache()
    monkeypatch.setattr(httpx, "get", lambda url, timeout=10.0: FakeResponse({"events": []}))
    assert live_scores.get_live_games({"DEN"}) == []
