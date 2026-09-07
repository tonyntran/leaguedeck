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
