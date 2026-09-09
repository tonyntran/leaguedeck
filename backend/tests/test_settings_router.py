TEST_PASSWORD = "testpassword123"


def test_settings_requires_auth(client):
    resp = client.get("/settings/sleeper")
    assert resp.status_code == 401


def test_put_and_get_sleeper_settings_roundtrip(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})

    put_resp = client.put(
        "/settings/sleeper", json={"username": "myuser", "league_ids": ["111", "222"]}
    )
    assert put_resp.status_code == 200

    get_resp = client.get("/settings/sleeper")
    assert get_resp.status_code == 200
    assert get_resp.json() == {"username": "myuser", "league_ids": ["111", "222"]}


def test_espn_settings_requires_auth(client):
    resp = client.get("/settings/espn")
    assert resp.status_code == 401


def test_put_and_get_espn_settings_roundtrip(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})

    put_resp = client.put(
        "/settings/espn",
        json={"espn_s2": "s2secret", "swid": "{GUID}", "league_ids": ["111", "222"]},
    )
    assert put_resp.status_code == 200

    get_resp = client.get("/settings/espn")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["league_ids"] == ["111", "222"]
    assert data["espn_s2_configured"] is True
    assert data["swid_configured"] is True
    # Must never echo the raw secret values anywhere in the response body --
    # not just under the expected field names.
    assert "s2secret" not in get_resp.text
    assert "{GUID}" not in get_resp.text


def test_get_espn_settings_before_any_put_reports_unconfigured(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})

    resp = client.get("/settings/espn")
    assert resp.status_code == 200
    assert resp.json() == {
        "league_ids": [],
        "espn_s2_configured": False,
        "swid_configured": False,
    }


def test_put_espn_settings_with_league_ids_only_preserves_existing_secrets(client):
    """Omitting the cookie fields entirely on a later PUT (e.g. a
    league-IDs-only edit) must not wipe the already-stored secrets."""
    client.post("/auth/login", json={"password": TEST_PASSWORD})
    client.put(
        "/settings/espn",
        json={"espn_s2": "s2secret", "swid": "{GUID}", "league_ids": ["111"]},
    )

    update_resp = client.put("/settings/espn", json={"league_ids": ["111", "222"]})
    assert update_resp.status_code == 200

    data = client.get("/settings/espn").json()
    assert data["league_ids"] == ["111", "222"]
    assert data["espn_s2_configured"] is True
    assert data["swid_configured"] is True


def test_put_espn_settings_with_blank_cookie_strings_preserves_existing_secrets(client):
    """Blank strings must be treated the same as omitted fields -- 'leave
    unchanged' -- not as an explicit request to overwrite with blank."""
    client.post("/auth/login", json={"password": TEST_PASSWORD})
    client.put(
        "/settings/espn",
        json={"espn_s2": "s2secret", "swid": "{GUID}", "league_ids": ["111"]},
    )

    client.put(
        "/settings/espn",
        json={"espn_s2": "", "swid": "", "league_ids": ["111"]},
    )

    data = client.get("/settings/espn").json()
    assert data["espn_s2_configured"] is True
    assert data["swid_configured"] is True


def test_yahoo_settings_requires_auth(client):
    resp = client.get("/settings/yahoo")
    assert resp.status_code == 401


def test_get_yahoo_settings_before_any_put_reports_unconfigured(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})

    resp = client.get("/settings/yahoo")
    assert resp.status_code == 200
    assert resp.json() == {
        "client_id": "",
        "league_ids": [],
        "client_secret_configured": False,
        "authorized": False,
    }


def test_put_and_get_yahoo_settings_roundtrip(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})

    put_resp = client.put(
        "/settings/yahoo",
        json={"client_id": "my-client-id", "client_secret": "shh", "league_ids": ["111", "222"]},
    )
    assert put_resp.status_code == 200

    get_resp = client.get("/settings/yahoo")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["client_id"] == "my-client-id"  # not secret, echoed back
    assert data["league_ids"] == ["111", "222"]
    assert data["client_secret_configured"] is True
    assert data["authorized"] is False  # PUT alone never touches tokens
    # The secret itself must never appear anywhere in the response body.
    assert "shh" not in get_resp.text


def test_put_yahoo_settings_with_league_ids_only_preserves_existing_secret(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})
    client.put(
        "/settings/yahoo",
        json={"client_id": "my-client-id", "client_secret": "shh", "league_ids": ["111"]},
    )

    update_resp = client.put(
        "/settings/yahoo", json={"client_id": "my-client-id", "league_ids": ["111", "222"]}
    )
    assert update_resp.status_code == 200

    data = client.get("/settings/yahoo").json()
    assert data["league_ids"] == ["111", "222"]
    assert data["client_secret_configured"] is True


def test_yahoo_authorize_url_requires_client_id(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})

    resp = client.get("/settings/yahoo/authorize-url")
    assert resp.status_code == 400


def test_yahoo_authorize_url_builds_from_stored_client_id(client):
    client.post("/auth/login", json={"password": TEST_PASSWORD})
    client.put(
        "/settings/yahoo",
        json={"client_id": "my-client-id", "client_secret": "shh", "league_ids": []},
    )

    resp = client.get("/settings/yahoo/authorize-url")
    assert resp.status_code == 200
    assert resp.json() == {
        "url": (
            "https://api.login.yahoo.com/oauth2/request_auth"
            "?client_id=my-client-id&redirect_uri=oob&response_type=code"
        )
    }


def test_yahoo_authorize_exchanges_code_and_stores_tokens(client, monkeypatch):
    from app.routers import settings as settings_router

    client.post("/auth/login", json={"password": TEST_PASSWORD})
    client.put(
        "/settings/yahoo",
        json={"client_id": "my-client-id", "client_secret": "shh", "league_ids": []},
    )

    monkeypatch.setattr(
        settings_router.yahoo,
        "exchange_code_for_tokens",
        lambda client_id, client_secret, code: {
            "access_token": "at-1",
            "refresh_token": "rt-1",
            "expires_in": 3600,
            "yahoo_guid": "GUID123",
        },
    )

    resp = client.post("/settings/yahoo/authorize", json={"code": "the-code"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    data = client.get("/settings/yahoo").json()
    assert data["authorized"] is True


def test_yahoo_authorize_returns_400_on_failed_exchange(client, monkeypatch):
    from app.routers import settings as settings_router

    client.post("/auth/login", json={"password": TEST_PASSWORD})
    client.put(
        "/settings/yahoo",
        json={"client_id": "my-client-id", "client_secret": "shh", "league_ids": []},
    )

    def raise_error(client_id, client_secret, code):
        raise Exception("invalid_grant")

    monkeypatch.setattr(settings_router.yahoo, "exchange_code_for_tokens", raise_error)

    resp = client.post("/settings/yahoo/authorize", json={"code": "bad-code"})
    assert resp.status_code == 400
    # The exchange failure reason must not leak the raw code or a token.
    assert "bad-code" not in resp.text
