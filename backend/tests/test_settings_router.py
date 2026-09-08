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
