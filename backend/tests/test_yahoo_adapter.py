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
